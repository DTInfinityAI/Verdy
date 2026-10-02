"""Learning from experts: recorded operator sessions as observation/action pairs.

Demonstrations are JSON Lines, one control step per line::

    {"session": "op-17", "scenario": {...params...}, "t": 0.1, "obs": {...}, "action": [0.4, 0.0]}

Export them from teleoperation logs, or record them with :func:`record_demonstrations`
by driving an expert policy (or a teleop bridge) through scenarios on a backend. A
trainer's ``pretrain`` uses them to give the policy a strong starting point before
feedback-driven training.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from verdy import policy as policy_api
from verdy.backends.base import Backend
from verdy.odd.model import ODD
from verdy.sampler.base import Scenario


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


class RecordingPolicy:
    """Wraps a policy and records every (observation, action) it sees."""

    def __init__(self, policy: Any, session: str = "session") -> None:
        self.policy = policy
        self.session = session
        self.steps: list[dict[str, Any]] = []
        self.context: dict[str, Any] = {}

    def reset(self, seed: int) -> None:
        policy_api.reset(self.policy, seed)

    def act(self, obs: Any) -> Any:
        action = policy_api.act(self.policy, obs)
        self.steps.append({"session": self.session, **self.context,
                           "obs": _plain(obs), "action": _plain(action)})
        return action


def record_demonstrations(
    odd: ODD,
    backend: Backend,
    expert: Any,
    scenarios: Sequence[Scenario],
    path: str | Path,
    every: int = 1,
    round_to: int | None = 4,
) -> int:
    """Run ``expert`` on each scenario and write its steps to ``path``. Returns the count.

    ``every`` keeps one step in ``every`` to keep files small.
    """
    backend.bind(odd)
    recorder = RecordingPolicy(expert)
    try:
        for s in scenarios:
            recorder.session = f"{s.id}"
            recorder.context = {"scenario": _plain(s.params)}
            backend.rollout(backend.build(s.to_dict()), recorder, s.seed)
    finally:
        backend.close()
    steps = recorder.steps[::every]
    if round_to is not None:
        steps = [_round(st, round_to) for st in steps]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for st in steps:
            fh.write(json.dumps(st, separators=(",", ":")) + "\n")
    return len(steps)


def _round(value: Any, nd: int) -> Any:
    if isinstance(value, float):
        return round(value, nd)
    if isinstance(value, dict):
        return {k: _round(v, nd) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v, nd) for v in value]
    return value


def load_demonstrations(paths: str | Path | Iterable[str | Path]) -> list[dict[str, Any]]:
    """Load demonstration steps from one or more JSONL files."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    steps = []
    for p in paths:
        for n, line in enumerate(Path(p).read_text("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            step = json.loads(line)
            if "obs" not in step or "action" not in step:
                raise ValueError(f"{p}:{n}: each demonstration step needs 'obs' and 'action'")
            steps.append(step)
    return steps
