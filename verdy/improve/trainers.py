"""Plug-and-play training modules.

A trainer is any object with::

    train(policy, data: TrainingData, ctx: TrainingContext) -> new_policy
    pretrain(policy, demonstrations, ctx) -> new_policy      # optional

``data`` holds everything the loop learned this cycle (diagnosis episodes, rewards,
preferences, the reward model, the training curriculum, demonstrations); ``ctx`` lets
the trainer roll out policies on training scenarios and score them with the cycle's
reward. Trainers never see certification scenarios.

Built in:

* :class:`ParameterSearchTrainer`: cross-entropy search over a policy's parameters,
  maximizing reward on the curriculum, with imitation pretraining on demonstrations.
  Needs no ML framework; good for tuning controllers and for demos.
* :class:`CommandTrainer`: exports the cycle's data to a directory and runs any external
  training command (an RL or RLHF framework, a GPU job, a script), then loads the policy
  it produced. This is how to plug in TRL, Stable-Baselines3, LeRobot or in-house code.
* Any Python class referenced as ``module:attribute`` in the loop config.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from verdy import policy as policy_api
from verdy.backends.base import Backend
from verdy.improve.episodes import Episode, rollout
from verdy.improve.preferences import Preference
from verdy.metrics.stl import STLSpec
from verdy.odd.model import ODD
from verdy.sampler.base import Scenario
from verdy.secrets import redact, subprocess_env


@dataclass
class TrainingData:
    cycle: int
    episodes: list[Episode]
    """Diagnosis runs of the current policy."""
    rewards: dict[str, float]
    """Reward of each diagnosis episode, by id."""
    preferences: list[Preference]
    """All human preferences collected so far."""
    reward_model: Any
    scenarios: list[Scenario]
    """Training curriculum: where to practice."""
    demonstrations: list[dict[str, Any]] = field(default_factory=list)
    failure_map: dict[str, Any] = field(default_factory=dict)


class TrainingContext:
    """What a trainer may use: rollouts on training scenarios, and the cycle's reward."""

    def __init__(self, odd: ODD, specs: list[STLSpec], backend: Backend,
                 reward: Callable[[Episode], float], work_dir: Path) -> None:
        self.odd, self.specs, self.backend = odd, specs, backend
        self.reward = reward
        self.work_dir = work_dir
        self.rollouts = 0

    def rollout(self, policy: Any, scenarios: Sequence[Scenario]) -> list[Episode]:
        self.rollouts += len(scenarios)
        episodes, _ = rollout(self.odd, self.specs, self.backend, policy, scenarios)
        return episodes

    def score(self, policy: Any, scenarios: Sequence[Scenario]) -> float:
        """Mean reward of ``policy`` on ``scenarios``."""
        episodes = self.rollout(policy, scenarios)
        return float(np.mean([self.reward(e) for e in episodes])) if episodes else 0.0


class Trainer(Protocol):
    def train(self, policy: Any, data: TrainingData, ctx: TrainingContext) -> Any: ...


class ParameterizedPolicy:
    """A policy built as ``factory(**params)``, exposing ``params`` for trainers."""

    def __init__(self, factory: Callable[..., Any], params: dict[str, float]) -> None:
        self.factory = factory
        self.params = {k: float(v) for k, v in params.items()}
        self.inner = factory(**self.params)

    def with_params(self, params: dict[str, float]) -> ParameterizedPolicy:
        return ParameterizedPolicy(self.factory, {**self.params, **params})

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["inner"], name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner(*args, **kwargs)

    def __repr__(self) -> str:
        return f"ParameterizedPolicy({self.params})"


class ParameterSearchTrainer:
    """Cross-entropy search over policy parameters.

    ``train`` maximizes the cycle's reward on the curriculum scenarios. ``pretrain`` fits
    the parameters to demonstrations by minimizing the squared error between the policy's
    actions and the expert's on the recorded observations (behavior cloning).

    Args:
        bounds: ``{param: [low, high]}`` for every parameter to search.
        iterations, population: cross-entropy iterations and candidates per iteration.
        elite_fraction: share of candidates used to refit the search distribution.
        scenarios_per_eval: training scenarios per candidate (default: all).
        init_sigma: initial search width, as a fraction of each range.
    """

    def __init__(self, bounds: dict[str, Sequence[float]], iterations: int = 4,
                 population: int = 12, elite_fraction: float = 0.25,
                 scenarios_per_eval: int | None = None, init_sigma: float = 0.25,
                 seed: int = 0) -> None:
        self.bounds = {k: (float(v[0]), float(v[1])) for k, v in bounds.items()}
        self.iterations = iterations
        self.population = population
        self.elite = max(2, int(round(elite_fraction * population)))
        self.scenarios_per_eval = scenarios_per_eval
        self.init_sigma = init_sigma
        self.rng = np.random.default_rng(seed)
        self.history: list[dict[str, Any]] = []

    def _check(self, policy: Any) -> ParameterizedPolicy:
        if not isinstance(policy, ParameterizedPolicy):
            raise TypeError("ParameterSearchTrainer needs a ParameterizedPolicy "
                            "(set policy_factory and initial_params in the loop config)")
        return policy

    def _search(self, start: dict[str, float], objective: Callable[[dict[str, float]], float]
                ) -> tuple[dict[str, float], float, float]:
        names = list(self.bounds)
        lo = np.array([self.bounds[n][0] for n in names])
        hi = np.array([self.bounds[n][1] for n in names])
        mu = np.clip(np.array([start.get(n, (lo[i] + hi[i]) / 2) for i, n in enumerate(names)]),
                     lo, hi)
        sigma = self.init_sigma * (hi - lo)
        start_score = objective(dict(zip(names, mu, strict=True)))
        best, best_score = mu.copy(), start_score
        for it in range(self.iterations):
            cands = np.clip(mu + sigma * self.rng.standard_normal((self.population, len(names))),
                            lo, hi)
            scores = np.array([objective(dict(zip(names, c, strict=True))) for c in cands])
            order = np.argsort(-scores)
            elite = cands[order[: self.elite]]
            if scores[order[0]] > best_score:
                best, best_score = cands[order[0]].copy(), float(scores[order[0]])
            mu = 0.7 * elite.mean(axis=0) + 0.3 * mu
            sigma = np.maximum(0.7 * elite.std(axis=0) + 0.3 * sigma, 0.02 * (hi - lo))
            self.history.append({"iteration": it, "best": float(scores[order[0]]),
                                 "mean": float(scores.mean())})
        return dict(zip(names, map(float, best), strict=True)), start_score, best_score

    def train(self, policy: Any, data: TrainingData, ctx: TrainingContext) -> Any:
        policy = self._check(policy)
        scenarios = data.scenarios[: self.scenarios_per_eval] if self.scenarios_per_eval \
            else data.scenarios

        def objective(params: dict[str, float]) -> float:
            return ctx.score(policy.with_params(params), scenarios)

        best, before, after = self._search(policy.params, objective)
        self.last = {"reward_before": before, "reward_after": after, "params": best}
        return policy.with_params(best) if after > before else policy

    def pretrain(self, policy: Any, demonstrations: Sequence[dict[str, Any]],
                 ctx: TrainingContext) -> Any:
        policy = self._check(policy)
        steps = list(demonstrations)
        if not steps:
            return policy
        target = [np.asarray(s["action"], dtype=float) for s in steps]

        def objective(params: dict[str, float]) -> float:
            cand = policy.with_params(params)
            policy_api.reset(cand, 0)
            err = 0.0
            for s, a in zip(steps, target, strict=True):
                err += float(np.sum((np.asarray(policy_api.act(cand, s["obs"]), float) - a) ** 2))
            return -err / len(steps)

        best, before, after = self._search(policy.params, objective)
        self.last_pretrain = {"imitation_mse_before": -before, "imitation_mse_after": -after,
                              "params": best}
        return policy.with_params(best) if after > before else policy

    def config(self) -> dict[str, Any]:
        return {"type": "parameter_search", "bounds": self.bounds,
                "iterations": self.iterations, "population": self.population}


class CommandTrainer:
    """Run an external training program on the cycle's exported data.

    Each cycle, the trainer writes to ``<work_dir>/cycle_<n>/data``:

    ``episodes.jsonl``     diagnosis runs (params, seed, robustness, features, reward)
    ``traces/<id>.json``   their traces
    ``preferences.jsonl``  all human preferences so far
    ``reward_model.json``  the fitted reward model summary
    ``scenarios.jsonl``    training curriculum (params and seeds)
    ``demonstrations.jsonl`` expert steps, if any
    ``manifest.json``      cycle number, file list, input policy reference

    then runs ``command`` with ``{data_dir}``, ``{out_dir}``, ``{policy_in}`` and
    ``{cycle}`` substituted, and calls ``loader(out_dir)`` to get the trained policy.
    Secrets listed in ``secrets`` are passed to the command's environment; nothing else
    from your environment is, except basic system variables and ``extra_env``.
    """

    def __init__(self, command: str | Sequence[str], loader: Callable[[Path], Any],
                 cwd: str | Path | None = None, secrets: Sequence[str] = (),
                 extra_env: dict[str, str] | None = None, timeout: float = 24 * 3600,
                 policy_ref: Callable[[Any], str] | None = None) -> None:
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        self.loader = loader
        self.cwd = Path(cwd) if cwd else None
        self.secrets = list(secrets)
        self.extra_env = dict(extra_env or {})
        self.timeout = timeout
        self.policy_ref = policy_ref or (
            lambda p: str(getattr(p, "checkpoint", policy_api.describe(p))))

    def export(self, policy: Any, data: TrainingData, ctx: TrainingContext) -> tuple[Path, Path]:
        base = ctx.work_dir / f"cycle_{data.cycle}"
        data_dir, out_dir = base / "data", base / "trained"
        (data_dir / "traces").mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "episodes.jsonl", "w", encoding="utf-8") as fh:
            for e in data.episodes:
                fh.write(json.dumps({**e.summary(), "reward": data.rewards.get(e.id)}) + "\n")
                if e.trace:
                    (data_dir / "traces" / f"{e.id}.json").write_text(json.dumps(e.trace))
        with open(data_dir / "preferences.jsonl", "w", encoding="utf-8") as fh:
            for p in data.preferences:
                fh.write(json.dumps(p.to_dict()) + "\n")
        model = data.reward_model.to_dict() if hasattr(data.reward_model, "to_dict") else {}
        (data_dir / "reward_model.json").write_text(json.dumps(model, indent=2))
        with open(data_dir / "scenarios.jsonl", "w", encoding="utf-8") as fh:
            for s in data.scenarios:
                fh.write(json.dumps(s.to_dict()) + "\n")
        if data.demonstrations:
            with open(data_dir / "demonstrations.jsonl", "w", encoding="utf-8") as fh:
                for st in data.demonstrations:
                    fh.write(json.dumps(st) + "\n")
        manifest = {"cycle": data.cycle, "policy_in": self.policy_ref(policy),
                    "files": sorted(p.name for p in data_dir.iterdir())}
        (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return data_dir, out_dir

    def train(self, policy: Any, data: TrainingData, ctx: TrainingContext) -> Any:
        data_dir, out_dir = self.export(policy, data, ctx)
        values = {"data_dir": str(data_dir), "out_dir": str(out_dir),
                  "policy_in": self.policy_ref(policy), "cycle": str(data.cycle)}
        cmd = [part.format(**values) for part in self.command]
        env = subprocess_env(required=self.secrets, extra=self.extra_env)
        proc = subprocess.run(cmd, cwd=self.cwd, env=env, capture_output=True, text=True,
                              timeout=self.timeout)
        log = redact((proc.stdout or "") + (proc.stderr or ""))
        (out_dir.parent / "train.log").write_text(log, "utf-8")
        if proc.returncode != 0:
            tail = "\n".join(log.strip().splitlines()[-15:])
            raise RuntimeError(f"training command exited with {proc.returncode}:\n{tail}")
        return self.loader(out_dir)

    def config(self) -> dict[str, Any]:
        return {"type": "command", "command": self.command, "secrets": self.secrets}
