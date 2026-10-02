"""Human feedback: operators compare pairs of runs, and a reward model learns from it.

1. :func:`select_pairs` picks pairs of runs worth asking about: ones the current reward
   model is least sure about, or (before a model exists) pairs with similar safety scores,
   where the rules alone cannot tell which is better.
2. A **labeler** collects the choice for each pair. Labelers are interchangeable:

   * :class:`FileLabeler` writes pairs to ``pending.jsonl`` for operators (a review tool or
     a spreadsheet) and reads their answers from ``labels.jsonl``, across sessions.
   * :class:`CLILabeler` asks in the terminal.
   * :class:`ScriptedLabeler` calls a function: for tests and simulated operators only.

3. :class:`BradleyTerryRewardModel` fits a reward over run features so that preferred
   runs score higher. It captures qualities that are hard to write as rules, such as
   smoothness or "how an expert would do it". Any object with ``fit(preferences)``,
   ``predict(features)`` and ``fitted`` can replace it.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from scipy.optimize import minimize

from verdy.improve.episodes import Episode

CHOICES = ("a", "b", "tie")


@dataclass
class Preference:
    pair_id: str
    a: str
    b: str
    choice: str
    """``"a"``, ``"b"`` or ``"tie"``."""
    labeler: str
    features_a: dict[str, float]
    features_b: dict[str, float]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Preference:
        return cls(**data)


def save_preferences(prefs: Sequence[Preference], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for p in prefs:
            fh.write(json.dumps(p.to_dict()) + "\n")


def load_preferences(path: str | Path) -> list[Preference]:
    path = Path(path)
    if not path.is_file():
        return []
    return [Preference.from_dict(json.loads(line))
            for line in path.read_text("utf-8").splitlines() if line.strip()]


# -- pair selection --------------------------------------------------------------------


def select_pairs(
    episodes: Sequence[Episode],
    n: int,
    rng: np.random.Generator,
    score: Callable[[Episode], float],
    model: Any = None,
    pool: int = 400,
) -> list[tuple[Episode, Episode]]:
    """Pick ``n`` informative pairs of runs to compare."""
    usable = [e for e in episodes if not e.error and e.features]
    if len(usable) < 2 or n <= 0:
        return []
    all_pairs = list(combinations(range(len(usable)), 2))
    idx = rng.choice(len(all_pairs), size=min(pool, len(all_pairs)), replace=False)
    candidates = [all_pairs[i] for i in idx]
    if model is not None and getattr(model, "fitted", False):
        def gap(pair: tuple[int, int]) -> float:  # closeness of predicted preference to 0.5
            return abs(model.prob_a_preferred(usable[pair[0]].features,
                                              usable[pair[1]].features) - 0.5)
    else:
        scores = [score(e) for e in usable]

        def gap(pair: tuple[int, int]) -> float:
            return abs(scores[pair[0]] - scores[pair[1]])
    candidates.sort(key=gap)
    chosen, used = [], set()
    for i, j in candidates:  # prefer pairs that do not reuse runs
        if i in used or j in used:
            continue
        chosen.append((usable[i], usable[j]))
        used.update((i, j))
        if len(chosen) == n:
            break
    return chosen


def pair_id(a: Episode, b: Episode, tag: str = "") -> str:
    return f"{tag}{a.id}~{b.id}"


# -- labelers ----------------------------------------------------------------------------


class Labeler(Protocol):
    name: str

    def label(self, pairs: Sequence[tuple[Episode, Episode]], tag: str = ""
              ) -> list[Preference]: ...


def _make(a: Episode, b: Episode, choice: str, labeler: str, tag: str, reason: str = ""
          ) -> Preference:
    if choice not in CHOICES:
        raise ValueError(f"preference must be one of {CHOICES}, got {choice!r}")
    return Preference(pair_id(a, b, tag), a.id, b.id, choice, labeler, a.features, b.features,
                      reason)


class ScriptedLabeler:
    """Labels pairs with ``fn(a, b) -> "a" | "b" | "tie"``. A stand-in for operators in
    tests and simulations; real feedback should come from people."""

    def __init__(self, fn: Callable[[Episode, Episode], str], name: str = "scripted") -> None:
        self.fn = fn
        self.name = name

    def label(self, pairs: Sequence[tuple[Episode, Episode]], tag: str = "") -> list[Preference]:
        return [_make(a, b, self.fn(a, b), self.name, tag) for a, b in pairs]


class FileLabeler:
    """Asynchronous labeling through files, for operators using any review tool.

    ``pending.jsonl`` gets one line per pair: ``pair_id`` and a summary of runs ``a`` and
    ``b`` (parameters, safety scores, features, and trace file paths when traces are
    saved). Operators append answers to ``labels.jsonl``::

        {"pair_id": "c1-s00003~s00017", "choice": "a", "reason": "smoother braking"}

    Answers can arrive later: unanswered pairs stay pending, and the loop uses whatever
    labels exist when it runs.
    """

    def __init__(self, directory: str | Path, name: str = "operators") -> None:
        self.dir = Path(directory)
        self.name = name

    def label(self, pairs: Sequence[tuple[Episode, Episode]], tag: str = "") -> list[Preference]:
        self.dir.mkdir(parents=True, exist_ok=True)
        traces = self.dir / "traces"
        pending_path, labels_path = self.dir / "pending.jsonl", self.dir / "labels.jsonl"
        pending = {}
        if pending_path.is_file():
            for line in pending_path.read_text("utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    pending[item["pair_id"]] = item
        lookup = {}
        for a, b in pairs:
            pid = pair_id(a, b, tag)
            lookup[pid] = (a, b)
            if pid in pending:
                continue
            item = {"pair_id": pid, "a": a.summary(), "b": b.summary()}
            for side, ep in (("a", a), ("b", b)):
                if ep.trace:
                    traces.mkdir(exist_ok=True)
                    tpath = traces / f"{tag}{ep.id}.json"
                    tpath.write_text(json.dumps(ep.trace), "utf-8")
                    item[side]["trace_file"] = str(tpath)
            pending[pid] = item
        pending_path.write_text("".join(json.dumps(v) + "\n" for v in pending.values()), "utf-8")

        out = []
        if labels_path.is_file():
            for line in labels_path.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                ans = json.loads(line)
                if ans.get("pair_id") in lookup:
                    a, b = lookup[ans["pair_id"]]
                    out.append(_make(a, b, ans["choice"], ans.get("labeler", self.name), tag,
                                     ans.get("reason", "")))
        return out


class CLILabeler:
    """Asks in the terminal which run of each pair is better."""

    def __init__(self, name: str = "cli", input_fn: Callable[[str], str] = input,
                 show: Sequence[str] | None = None) -> None:
        self.name = name
        self.input = input_fn
        self.show = show

    def _describe(self, ep: Episode) -> str:
        feats = {k: v for k, v in ep.features.items()
                 if self.show is None or any(k.startswith(s) for s in self.show)}
        parts = [f"{k}={v:.3g}" for k, v in sorted(feats.items())]
        return f"{ep.id} params={ep.scenario.params}\n    " + "  ".join(parts)

    def label(self, pairs: Sequence[tuple[Episode, Episode]], tag: str = "") -> list[Preference]:
        out = []
        for a, b in pairs:
            print(f"\nA: {self._describe(a)}\nB: {self._describe(b)}")
            answer = ""
            while answer not in ("a", "b", "t", "s"):
                answer = self.input("Better run? [a/b/t(ie)/s(kip)] ").strip().lower()[:1]
            if answer != "s":
                out.append(_make(a, b, "tie" if answer == "t" else answer, self.name, tag))
        return out


# -- reward model -------------------------------------------------------------------------


class BradleyTerryRewardModel:
    """Linear reward ``w · standardized(features)`` fitted to pairwise preferences.

    Bradley-Terry: ``P(a preferred over b) = sigmoid(r(a) - r(b))``. Ties count as half a
    win each way. Fitted by L2-regularized maximum likelihood.
    """

    def __init__(self, l2: float = 1.0, features: Sequence[str] | None = None,
                 min_preferences: int = 5) -> None:
        self.l2 = l2
        self.feature_filter = list(features) if features else None
        self.min_preferences = min_preferences
        self.names: list[str] = []
        self.mean = self.scale = self.w = np.zeros(0)
        self.fitted = False
        self.train_accuracy: float | None = None
        self.n_preferences = 0

    def _matrix(self, feats: Sequence[dict[str, float]]) -> np.ndarray:
        return np.array([[f.get(n, 0.0) for n in self.names] for f in feats], dtype=float)

    def _z(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.scale

    def fit(self, prefs: Sequence[Preference]) -> BradleyTerryRewardModel:
        prefs = list(prefs)
        self.n_preferences = len(prefs)
        if len(prefs) < self.min_preferences:
            return self
        names = sorted({k for p in prefs for k in (*p.features_a, *p.features_b)})
        if self.feature_filter:
            names = [n for n in names if any(n.startswith(f) for f in self.feature_filter)]
        self.names = names
        xa = self._matrix([p.features_a for p in prefs])
        xb = self._matrix([p.features_b for p in prefs])
        both = np.vstack([xa, xb])
        self.mean = both.mean(axis=0)
        self.scale = np.where(both.std(axis=0) > 1e-9, both.std(axis=0), 1.0)
        d = self._z(xa) - self._z(xb)
        y = np.array([{"a": 1.0, "b": 0.0, "tie": 0.5}[p.choice] for p in prefs])

        def loss(w: np.ndarray) -> tuple[float, np.ndarray]:
            s = d @ w
            p = 1.0 / (1.0 + np.exp(-s))
            eps = 1e-12
            nll = -np.sum(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))
            grad = d.T @ (p - y) + self.l2 * w
            return nll + 0.5 * self.l2 * w @ w, grad

        res = minimize(loss, np.zeros(len(names)), jac=True, method="L-BFGS-B")
        self.w = res.x
        self.fitted = True
        decided = y != 0.5
        if decided.any():
            self.train_accuracy = float(np.mean(((d @ self.w) > 0)[decided] == (y[decided] == 1)))
        return self

    def predict(self, features: dict[str, float]) -> float:
        if not self.fitted:
            return 0.0
        return float(self._z(self._matrix([features])[0]) @ self.w)

    def prob_a_preferred(self, fa: dict[str, float], fb: dict[str, float]) -> float:
        return float(1.0 / (1.0 + np.exp(-(self.predict(fa) - self.predict(fb)))))

    def to_dict(self) -> dict[str, Any]:
        """Model summary for the ledger: the most influential features first."""
        order = np.argsort(-np.abs(self.w)) if self.fitted else []
        return {
            "type": "bradley_terry", "fitted": self.fitted, "l2": self.l2,
            "n_preferences": self.n_preferences, "train_accuracy": self.train_accuracy,
            "weights": {self.names[i]: round(float(self.w[i]), 4) for i in order},
        }
