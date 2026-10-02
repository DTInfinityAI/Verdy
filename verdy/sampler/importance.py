"""Failure-seeking importance sampling with the cross-entropy method.

The sampler starts from the nominal ODD distribution. After each batch, it refits a
proposal distribution to the scenarios that came closest to failing (lowest robustness),
so later batches concentrate on the failure region.

Scenarios are drawn from a *defensive mixture* ``m = λ·p + (1 - λ)·q`` of the nominal
distribution ``p`` and the adapted proposal ``q``. Every scenario carries the likelihood
ratio ``weight = p(x) / m(x)``, which is at most ``1/λ``, so failure probabilities
estimated from the weighted runs remain estimates under the *nominal* distribution and no
single run can dominate the estimate.

Numeric proposals are fitted in each parameter's *quantile space* ``u = F_nominal(x)``,
where the nominal distribution is uniform on [0, 1]. A truncated normal over ``u`` can
concentrate near either end of the domain whatever the nominal shape (log-uniform,
normal, beta, ...), and its density in ``x`` is ``q(x) = q_u(F(x)) · p(x)``.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import stats

from verdy.sampler.base import Sampler, SamplingError, Scenario
from verdy.sampler.distributions import DiscreteDistribution, NumericDistribution

_UNIFORM_SD = 1.0 / np.sqrt(12.0)


class _QuantileProposal:
    """Truncated normal over the nominal quantile ``u = F(x)`` in [0, 1]."""

    def __init__(self, nominal: NumericDistribution, mu: float, sigma: float) -> None:
        self.nominal, self.mu, self.sigma = nominal, mu, sigma
        self._u = stats.truncnorm((0.0 - mu) / sigma, (1.0 - mu) / sigma, loc=mu, scale=sigma)

    def sample(self, rng: np.random.Generator) -> float:
        u = float(np.clip(self._u.ppf(rng.random()), 0.0, 1.0))
        return self.nominal.ppf(u)

    def pdf(self, x: float) -> float:
        return float(self._u.pdf(self.nominal.cdf(x))) * self.nominal.pdf(x)


class ImportanceSampler(Sampler):
    """Cross-entropy importance sampler.

    Args:
        elite_fraction: share of each batch (lowest scores) used to refit the proposal.
        defensive: share λ of each batch drawn from the nominal distribution (0-1).
        smoothing: weight of the new fit vs. the previous proposal (0-1).
        min_sigma: lower bound on the width of numeric proposals, in quantile units.
        min_prob: lower bound on proposal probability of any categorical value.
    """

    adaptive = True

    def __init__(
        self,
        odd: Any,
        seed: int = 0,
        elite_fraction: float = 0.2,
        defensive: float = 0.3,
        smoothing: float = 0.7,
        min_sigma: float = 0.05,
        min_prob: float = 0.02,
        **kwargs: Any,
    ) -> None:
        super().__init__(odd, seed=seed, **kwargs)
        if not 0 < defensive <= 1:
            raise ValueError("defensive must be in (0, 1]")
        self.elite_fraction = elite_fraction
        self.defensive = defensive
        self.smoothing = smoothing
        self.min_sigma = min_sigma
        self.min_prob = min_prob
        self.proposal: dict[str, Any] = dict(self.nominal)
        self.iteration = 0

    def config(self) -> dict[str, Any]:
        return {
            **super().config(),
            "elite_fraction": self.elite_fraction,
            "defensive": self.defensive,
            "smoothing": self.smoothing,
            "min_sigma": self.min_sigma,
            "min_prob": self.min_prob,
        }

    def _weight(self, params: dict[str, Any]) -> float:
        """p(x) / (λ p(x) + (1 - λ) q(x)), computed via log q/p for numerical stability."""
        log_q_over_p = 0.0
        for name, value in params.items():
            p = self.nominal[name].pdf(value)
            if p <= 0:
                return 0.0
            q = self.proposal[name].pdf(value)
            log_q_over_p += np.log(max(q, 1e-300)) - np.log(p)
        ratio = float(np.exp(min(log_q_over_p, 700.0)))
        return 1.0 / (self.defensive + (1.0 - self.defensive) * ratio)

    def sample(self, n: int) -> list[Scenario]:
        out = []
        for _ in range(n):
            source = self.nominal if self.rng.random() < self.defensive else self.proposal
            for _attempt in range(self.max_rejections):
                params = {name: d.sample(self.rng) for name, d in source.items()}
                if self._feasible(params):
                    break
            else:
                raise SamplingError("proposal produced no feasible scenario")
            out.append(self._make(params, weight=self._weight(params), iteration=self.iteration))
        return out

    def update(self, scenarios: Sequence[Scenario], scores: Sequence[float]) -> None:
        scores_arr = np.asarray(scores, dtype=float)
        valid = np.isfinite(scores_arr)
        if valid.sum() < 2:
            return
        scen = [s for s, ok in zip(scenarios, valid, strict=True) if ok]
        scores_arr = scores_arr[valid]
        n_elite = max(2, int(np.ceil(self.elite_fraction * len(scen))))
        # Every failure is elite; otherwise take the lowest-scoring fraction.
        threshold = max(np.sort(scores_arr)[n_elite - 1], 0.0)
        elite_idx = np.flatnonzero(scores_arr <= threshold)
        elites = [scen[i] for i in elite_idx]
        w = np.array([s.weight for s in elites], dtype=float)
        w = w / w.sum() if w.sum() > 0 else np.full(len(elites), 1 / len(elites))
        a = self.smoothing

        for name, nominal in self.nominal.items():
            values = [s.params[name] for s in elites]
            old = self.proposal[name]
            if isinstance(nominal, NumericDistribution):
                u = np.array([nominal.cdf(v) for v in values])
                mu_new = float(np.sum(w * u))
                sd_new = float(np.sqrt(np.sum(w * (u - mu_new) ** 2)))
                if isinstance(old, _QuantileProposal):
                    mu_old, sd_old = old.mu, old.sigma
                else:
                    mu_old, sd_old = 0.5, _UNIFORM_SD
                mu = a * mu_new + (1 - a) * mu_old
                sigma = max(a * sd_new + (1 - a) * sd_old, self.min_sigma)
                self.proposal[name] = _QuantileProposal(nominal, mu, sigma)
            else:
                assert isinstance(nominal, DiscreteDistribution)
                freq = np.array([
                    sum(wi for wi, v in zip(w, values, strict=True) if v == val)
                    for val in nominal.values
                ])
                old_p = old.probs
                probs = np.maximum(a * freq + (1 - a) * old_p, self.min_prob)
                self.proposal[name] = DiscreteDistribution(
                    nominal.values, list(probs / probs.sum()), "proposal"
                )
        self.iteration += 1
