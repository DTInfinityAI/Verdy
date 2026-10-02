"""Common adapter every execution backend implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from verdy.odd.model import ODD


class TraceError(ValueError):
    """Raised when a backend returns a malformed trace."""


class Backend(ABC):
    """Turns scenarios into rollouts.

    A *scenario* is a dict with ``id``, ``params`` (ODD parameter values), ``seed`` and
    ``metadata``. A *trace* is a dict with a ``"time"`` list (seconds, uniformly sampled)
    and one equally long list per signal, e.g. ``{"time": [...], "speed": [...]}``.
    """

    name: str = "backend"
    odd: ODD | None = None

    def bind(self, odd: ODD) -> None:
        """Called once before any rollout with the ODD under test."""
        self.odd = odd

    def sim_params(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """Scenario parameters keyed by each parameter's ``grounding.sim`` name, if set."""
        params = scenario["params"]
        if self.odd is None:
            return dict(params)
        out = {}
        for key, value in params.items():
            param = self.odd[key] if key in self.odd else None
            out[(param.grounding.get("sim") if param else None) or key] = value
        return out

    @abstractmethod
    def build(self, scenario: dict) -> object:
        """Realize a sampled scenario as a runnable environment."""

    @abstractmethod
    def rollout(self, env: object, policy: object, seed: int) -> dict:
        """Run the policy and return a time-stamped trace of signals."""

    def close(self) -> None:
        """Release simulator resources. Called once after the last rollout."""


def validate_trace(trace: dict[str, Any], required: set[str] | None = None) -> dict[str, list]:
    """Check a trace is well formed and return it with plain float lists."""
    if "time" not in trace:
        raise TraceError("trace has no 'time' signal")
    time = np.asarray(trace["time"], dtype=float)
    if time.ndim != 1 or len(time) < 2:
        raise TraceError("trace 'time' must be a list of at least two samples")
    steps = np.diff(time)
    if np.any(steps <= 0):
        raise TraceError("trace 'time' must be strictly increasing")
    if not np.allclose(steps, steps[0], rtol=1e-3, atol=1e-9):
        raise TraceError("trace 'time' must be uniformly sampled")
    out: dict[str, list] = {"time": time.tolist()}
    for key, values in trace.items():
        if key == "time":
            continue
        arr = np.asarray(values, dtype=float)
        if arr.shape != time.shape:
            raise TraceError(f"signal {key!r} has {arr.size} samples, expected {time.size}")
        out[key] = arr.tolist()
    missing = (required or set()) - set(out)
    if missing:
        raise TraceError("trace is missing signals: " + ", ".join(sorted(missing)))
    return out
