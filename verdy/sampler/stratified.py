"""Stratified (Latin hypercube) sampling for coverage."""
from __future__ import annotations

from typing import Any

from verdy.sampler.base import Sampler, Scenario


class StratifiedSampler(Sampler):
    """Latin hypercube sampling in each parameter's quantile space.

    Each batch of ``n`` scenarios places exactly one sample in each of ``n`` equal-probability
    strata of every parameter, so the domain is covered evenly even with small budgets.
    Marginals still follow the nominal distribution, so failure-rate estimates stay unbiased.
    If a stratified point violates a constraint, that row is redrawn at random.
    """

    def sample(self, n: int) -> list[Scenario]:
        if n <= 0:
            return []
        names = list(self.nominal)
        strata = {name: self.rng.permutation(n) for name in names}
        out = []
        for row in range(n):
            params: dict[str, Any] = {}
            for name in names:
                u = (strata[name][row] + self.rng.random()) / n
                params[name] = self.nominal[name].ppf(u)
            stratified = self._feasible(params)
            if not stratified:
                params = self._draw_nominal()
            out.append(self._make(params, stratified=stratified))
        return out
