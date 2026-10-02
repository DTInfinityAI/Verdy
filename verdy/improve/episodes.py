"""Episodes: evaluated runs in the form the improvement loop learns from."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from verdy.backends.base import Backend
from verdy.harness import EvaluationResult, evaluate
from verdy.metrics.stl import STLSpec
from verdy.odd.model import ODD
from verdy.sampler.base import Scenario
from verdy.sampler.fixed import FixedScenarioSampler
from verdy.verdict import VerdictConfig

ROBUSTNESS_CLIP = 5.0


@dataclass
class Episode:
    """One evaluated run: scenario, safety scores, trace and derived features."""

    scenario: Scenario
    robustness: dict[str, float]
    violated: list[str]
    failed: bool
    error: str | None
    trace: dict[str, list[float]] | None
    features: dict[str, float] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.scenario.id

    def summary(self) -> dict[str, Any]:
        """What an operator or an external trainer sees about this run."""
        return {
            "id": self.id,
            "params": self.scenario.params,
            "seed": self.scenario.seed,
            "robustness": self.robustness,
            "violated": self.violated,
            "failed": self.failed,
            "error": self.error,
            "features": self.features,
        }


def run_features(robustness: dict[str, float], trace: dict[str, list[float]] | None
                 ) -> dict[str, float]:
    """Numeric description of a run, used by reward models.

    Per spec: clipped robustness. Per trace signal: min, max, mean, and roughness
    (mean absolute rate of change), which captures smoothness.
    """
    feats: dict[str, float] = {}
    for name, value in robustness.items():
        v = value if np.isfinite(value) else -ROBUSTNESS_CLIP
        feats[f"rob:{name}"] = float(np.clip(v, -ROBUSTNESS_CLIP, ROBUSTNESS_CLIP))
    if trace:
        time = np.asarray(trace["time"], dtype=float)
        dt = float(time[1] - time[0]) if len(time) > 1 else 1.0
        for key, values in trace.items():
            if key == "time":
                continue
            x = np.asarray(values, dtype=float)
            feats[f"{key}:min"] = float(x.min())
            feats[f"{key}:max"] = float(x.max())
            feats[f"{key}:mean"] = float(x.mean())
            feats[f"{key}:rough"] = float(np.mean(np.abs(np.diff(x))) / dt) if len(x) > 1 else 0.0
    return feats


def episodes_from(result: EvaluationResult) -> list[Episode]:
    out = []
    for r in result.runs:
        trace = result.traces.get(r.scenario.id)
        out.append(Episode(
            scenario=r.scenario, robustness=dict(r.robustness), violated=list(r.violated),
            failed=r.failed, error=r.error, trace=trace,
            features=run_features(r.robustness, trace) if not r.error else {},
        ))
    return out


def rollout(
    odd: ODD,
    specs: list[STLSpec],
    backend: Backend,
    policy: Any,
    scenarios: Sequence[Scenario],
    verdict: VerdictConfig | None = None,
) -> tuple[list[Episode], EvaluationResult]:
    """Run ``policy`` on exactly these scenarios and return episodes with traces."""
    sampler = FixedScenarioSampler(odd, scenarios)
    result = evaluate(odd, specs, backend, policy, sampler, len(scenarios), verdict,
                      keep_traces=True)
    return episodes_from(result), result
