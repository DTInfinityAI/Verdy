"""Replay a fixed list of scenarios, e.g. a training curriculum."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from verdy.odd.model import ODD
from verdy.sampler.base import Sampler, Scenario


class FixedScenarioSampler(Sampler):
    """Yields the given scenarios in order, unchanged (ids, seeds and weights kept)."""

    def __init__(self, odd: ODD, scenarios: Sequence[Scenario], seed: int = 0,
                 **kwargs: Any) -> None:
        super().__init__(odd, seed=seed, **kwargs)
        self.scenarios = list(scenarios)
        self._next = 0

    def __len__(self) -> int:
        return len(self.scenarios)

    def config(self) -> dict[str, Any]:
        return {**super().config(), "n_scenarios": len(self.scenarios)}

    def sample(self, n: int) -> list[Scenario]:
        out = self.scenarios[self._next : self._next + n]
        self._next += len(out)
        return out
