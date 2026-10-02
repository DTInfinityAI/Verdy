"""Common sampler interface and the Scenario record."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from verdy.odd.constraints import compile_constraints, satisfies
from verdy.odd.model import ODD
from verdy.sampler.distributions import distribution_for


@dataclass
class Scenario:
    """One concrete point in the ODD, plus the seed used to run it."""

    id: str
    params: dict[str, Any]
    seed: int
    weight: float = 1.0
    """Likelihood ratio nominal/proposal; 1.0 unless importance sampling is used."""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "params": _plain(self.params),
            "seed": self.seed,
            "weight": self.weight,
            "metadata": _plain(self.metadata),
        }


def _plain(value: Any) -> Any:
    """Convert numpy scalars to plain Python values for JSON."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


class SamplingError(RuntimeError):
    """Raised when a sampler cannot produce scenarios that satisfy the ODD constraints."""


class Sampler(ABC):
    """Generates scenarios from an ODD.

    Samplers are deterministic given ``seed``: the same ODD and seed always produce the
    same scenarios in the same order.
    """

    adaptive: bool = False
    """Adaptive samplers expect :meth:`update` to be called after each batch."""

    def __init__(self, odd: ODD, seed: int = 0, max_rejections: int = 10_000) -> None:
        self.odd = odd
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.max_rejections = max_rejections
        self.nominal = {p.name: distribution_for(p) for p in odd.parameters}
        self.constraints = compile_constraints(odd.constraints)
        self._count = 0

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def sample(self, n: int) -> list[Scenario]:
        """Return the next ``n`` scenarios."""

    def update(self, scenarios: Sequence[Scenario], scores: Sequence[float]) -> None:
        """Feed back each scenario's score (lower = closer to failure). No-op by default."""

    def config(self) -> dict[str, Any]:
        """Settings recorded in the evidence ledger."""
        return {"sampler": self.name, "seed": self.seed}

    def _feasible(self, params: dict[str, Any]) -> bool:
        return satisfies(self.constraints, params)

    def _make(self, params: dict[str, Any], weight: float = 1.0, **metadata: Any) -> Scenario:
        scenario = Scenario(
            id=f"s{self._count:05d}",
            params=_plain(params),
            seed=int(self.rng.integers(0, 2**31 - 1)),
            weight=float(weight),
            metadata=metadata,
        )
        self._count += 1
        return scenario

    def _draw_nominal(self) -> dict[str, Any]:
        for _ in range(self.max_rejections):
            params = {name: d.sample(self.rng) for name, d in self.nominal.items()}
            if self._feasible(params):
                return params
        raise SamplingError(
            f"no scenario satisfied the ODD constraints after {self.max_rejections} draws"
        )
