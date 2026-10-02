"""Scenario generation: stratified coverage, log replay, importance sampling."""
from typing import Any

from verdy.odd.model import ODD
from verdy.sampler.base import Sampler, SamplingError, Scenario
from verdy.sampler.distributions import DistributionError, distribution_for
from verdy.sampler.fixed import FixedScenarioSampler
from verdy.sampler.importance import ImportanceSampler
from verdy.sampler.monte_carlo import MonteCarloSampler
from verdy.sampler.replay import LogReplaySampler
from verdy.sampler.stratified import StratifiedSampler

SAMPLERS: dict[str, type[Sampler]] = {
    "monte_carlo": MonteCarloSampler,
    "stratified": StratifiedSampler,
    "importance": ImportanceSampler,
    "replay": LogReplaySampler,
}


def make_sampler(kind: str, odd: ODD, **options: Any) -> Sampler:
    """Create a sampler by name: monte_carlo, stratified, importance or replay."""
    try:
        cls = SAMPLERS[kind]
    except KeyError:
        raise ValueError(f"unknown sampler {kind!r}; choose from {', '.join(SAMPLERS)}") from None
    return cls(odd, **options)


__all__ = [
    "SAMPLERS",
    "DistributionError",
    "FixedScenarioSampler",
    "ImportanceSampler",
    "LogReplaySampler",
    "MonteCarloSampler",
    "Sampler",
    "SamplingError",
    "Scenario",
    "StratifiedSampler",
    "distribution_for",
    "make_sampler",
]
