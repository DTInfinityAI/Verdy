"""Targeted practice: find where a policy is weak and generate more of those situations."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from verdy.improve.episodes import Episode
from verdy.odd.model import ODD
from verdy.sampler.base import Scenario
from verdy.sampler.importance import ImportanceSampler
from verdy.verdict.coverage import _cell_of, _cells


def failure_map(odd: ODD, episodes: Sequence[Episode], bins: int = 5) -> dict[str, Any]:
    """Failure rate per parameter cell: where in the ODD the policy fails.

    Returns ``{param: [{"cell", "runs", "failures", "rate"}, ...]}`` with cells sorted by
    failure rate, plus ``"weakest"``: the ten cells with the highest rates.
    """
    out: dict[str, Any] = {}
    flat = []
    for p in odd.parameters:
        cells = _cells(p, bins)
        counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for e in episodes:
            idx = _cell_of(p, e.scenario.params.get(p.name), bins)
            if idx is not None:
                counts[idx][0] += 1
                counts[idx][1] += int(e.failed)
        rows = [
            {"cell": cells[i], "runs": n, "failures": f, "rate": round(f / n, 4)}
            for i, (n, f) in sorted(counts.items())
        ]
        rows.sort(key=lambda r: -r["rate"])
        out[p.name] = rows
        flat += [{"parameter": p.name, **r} for r in rows if r["failures"]]
    flat.sort(key=lambda r: (-r["rate"], -r["failures"]))
    out["weakest"] = flat[:10]
    return out


class FailureFocusedCurriculum:
    """Training scenarios concentrated where the policy fails or nearly fails.

    Fits a cross-entropy proposal to the lowest-robustness diagnosis runs (the same
    machinery as :class:`verdy.sampler.ImportanceSampler`) and draws ``focus`` of each
    batch from it; the rest come from the nominal ODD, so the policy does not forget the
    common cases.

    Args:
        focus: share of training scenarios drawn near failures (0-1).
        elite_fraction: share of diagnosis runs treated as "near failure".
        seed: training scenarios use their own seed stream, separate from certification.
    """

    def __init__(self, odd: ODD, focus: float = 0.7, elite_fraction: float = 0.25,
                 seed: int = 1_000_003, fail_on: Sequence[str] | None = None) -> None:
        if not 0 <= focus < 1:
            raise ValueError("focus must be in [0, 1)")
        self.odd = odd
        self.focus = focus
        self.seed = seed
        self.fail_on = list(fail_on) if fail_on else None
        self.sampler = ImportanceSampler(odd, seed=seed, elite_fraction=elite_fraction,
                                         defensive=1.0 - focus)
        self.batches = 0

    def fit(self, episodes: Sequence[Episode]) -> None:
        usable = [e for e in episodes if not e.error and e.robustness]
        scores = []
        for e in usable:
            names = [n for n in e.robustness if self.fail_on is None or n in self.fail_on]
            scores.append(min(e.robustness[n] for n in names) if names else 0.0)
        self.sampler.update([e.scenario for e in usable], scores)

    def sample(self, n: int, prefix: str = "train") -> list[Scenario]:
        self.batches += 1
        out = self.sampler.sample(n)
        for s in out:
            s.id = f"{prefix}{self.batches}-{s.id}"
        return out

    def config(self) -> dict[str, Any]:
        return {"type": "failure_focused", "focus": self.focus, "seed": self.seed,
                **self.sampler.config()}
