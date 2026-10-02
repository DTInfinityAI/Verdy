"""Rewards: turn Verdy's safety scores (and learned preferences) into training signal."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from verdy.improve.episodes import Episode
from verdy.metrics.stl import STLSpec

SEVERITY_WEIGHTS = {"critical": 1.0, "major": 0.5, "minor": 0.2}


class Reward(Protocol):
    def __call__(self, episode: Episode) -> float: ...


class SafetyMarginReward:
    """Reward from STL robustness that keeps paying until a safety margin is reached.

    Per spec, the score is ``min(robustness, margin) / margin``: a run that only scrapes
    a pass (robustness just above 0) earns almost nothing, and the reward keeps rising
    until the run clears the spec by ``margin``. Violations score down to -1 and pay an
    extra ``violation_penalty``. Specs are weighted by severity. Errored runs get the
    lowest reward.

    Args:
        margin: robustness at which a spec earns full reward (same units as the spec), or
            a dict of per-spec margins.
        weights: weight per severity.
        violation_penalty: extra penalty for each violated spec, times its weight.
    """

    def __init__(
        self,
        specs: Sequence[STLSpec],
        margin: float | dict[str, float] = 0.3,
        weights: dict[str, float] | None = None,
        violation_penalty: float = 1.0,
    ) -> None:
        self.specs = list(specs)
        self.margin = margin
        self.weights = {**SEVERITY_WEIGHTS, **(weights or {})}
        self.violation_penalty = violation_penalty
        self._total = sum(self.weights[s.severity] for s in self.specs) or 1.0

    def _margin(self, name: str) -> float:
        m = self.margin.get(name, 0.3) if isinstance(self.margin, dict) else self.margin
        if m <= 0:
            raise ValueError("safety margins must be positive")
        return m

    def __call__(self, episode: Episode) -> float:
        floor = -1.0 - self.violation_penalty
        if episode.error:
            return floor
        total = 0.0
        for spec in self.specs:
            w = self.weights[spec.severity]
            r = episode.robustness.get(spec.name, float("nan"))
            if not r == r:  # NaN
                total += w * floor
                continue
            score = max(min(r, self._margin(spec.name)) / self._margin(spec.name), -1.0)
            if r < 0:
                score -= self.violation_penalty
            total += w * score
        return total / self._total

    def config(self) -> dict[str, Any]:
        return {"type": "safety_margin", "margin": self.margin, "weights": self.weights,
                "violation_penalty": self.violation_penalty}


class PreferenceReward:
    """Bounded reward from a learned preference model (see :mod:`verdy.improve.preferences`).

    A learned reward is unbounded, and an optimizer will happily exploit it at the expense
    of everything else (reward hacking). So predictions are standardized against a
    reference set of runs (:meth:`calibrate`, done every cycle on the diagnosis runs) and
    squashed with ``tanh`` into (-1, 1). Preferences then shape *how* the robot behaves,
    but cannot outweigh a safety violation in the combined reward.
    """

    def __init__(self, model: Any) -> None:
        self.model = model
        self.center, self.scale = 0.0, 1.0

    def calibrate(self, episodes: Sequence[Episode]) -> PreferenceReward:
        preds = [self.model.predict(e.features) for e in episodes
                 if not e.error and e.features]
        if len(preds) >= 2 and getattr(self.model, "fitted", False):
            self.center = float(np.mean(preds))
            self.scale = float(np.std(preds)) or 1.0
        return self

    def __call__(self, episode: Episode) -> float:
        if episode.error or not getattr(self.model, "fitted", False):
            return 0.0
        z = (float(self.model.predict(episode.features)) - self.center) / self.scale
        return float(np.tanh(z))

    def config(self) -> dict[str, Any]:
        return {"type": "preference", "bounded": "tanh", "center": self.center,
                "scale": self.scale}


class CompositeReward:
    """Weighted sum of rewards: ``sum(weight * reward(episode))``."""

    def __init__(self, parts: Sequence[tuple[Any, float]]) -> None:
        self.parts = [(r, float(w)) for r, w in parts if w]

    def __call__(self, episode: Episode) -> float:
        return sum(w * r(episode) for r, w in self.parts)

    def components(self, episode: Episode) -> dict[str, float]:
        return {type(r).__name__: r(episode) for r, _ in self.parts}

    def config(self) -> dict[str, Any]:
        return {"type": "composite", "parts": [
            {"weight": w, **(r.config() if hasattr(r, "config") else {"type": type(r).__name__})}
            for r, w in self.parts]}
