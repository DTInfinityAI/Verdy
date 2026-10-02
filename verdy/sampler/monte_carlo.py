"""Plain Monte Carlo sampling from the nominal ODD distribution."""
from __future__ import annotations

from verdy.sampler.base import Sampler, Scenario


class MonteCarloSampler(Sampler):
    """Independent draws from each parameter's nominal distribution.

    Scenarios that violate an ODD constraint are rejected and redrawn, so samples follow
    the nominal distribution conditioned on the constraints.
    """

    def sample(self, n: int) -> list[Scenario]:
        return [self._make(self._draw_nominal()) for _ in range(n)]
