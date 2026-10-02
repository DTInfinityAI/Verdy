"""Nominal sampling distributions for ODD parameters.

The ``distribution`` field of an ODD parameter is parsed into a distribution over the
parameter's domain. Numeric distributions are always truncated to the parameter ``range``.

=================  =====================  ===========================================
Parameter type     ``distribution``       Meaning
=================  =====================  ===========================================
continuous,        ``uniform`` (default)  Uniform over ``range``
temporal           ``normal(mu, sigma)``  Normal truncated to ``range``
                   ``loguniform``         Log-uniform over ``range`` (needs min > 0)
                   ``triangular(mode)``   Triangular over ``range`` peaking at ``mode``
                   ``beta(a, b)``         Beta(a, b) scaled to ``range``
categorical        ``uniform`` (default)  Uniform over ``values``, or ``weights`` if set
boolean            ``bernoulli(p)``       ``True`` with probability ``p`` (default 0.5)
=================  =====================  ===========================================
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
from scipy import stats

from verdy.odd.model import Parameter

_CALL = re.compile(r"^\s*([a-z_]+)\s*(?:\((.*)\))?\s*$")


class DistributionError(ValueError):
    """Raised when a parameter's distribution is invalid."""


def _parse(text: str) -> tuple[str, list[float]]:
    match = _CALL.match(text)
    if not match:
        raise DistributionError(f"cannot parse distribution {text!r}")
    name, args = match.group(1), match.group(2)
    try:
        values = [float(a) for a in args.split(",")] if args and args.strip() else []
    except ValueError as exc:
        raise DistributionError(f"distribution arguments must be numbers: {text!r}") from exc
    return name, values


class NumericDistribution:
    """A distribution over a closed interval ``[lo, hi]``."""

    def __init__(self, frozen: Any, lo: float, hi: float, label: str) -> None:
        self._dist = frozen
        self.lo, self.hi = lo, hi
        self.label = label

    def sample(self, rng: np.random.Generator) -> float:
        return self.ppf(rng.random())

    def ppf(self, u: float) -> float:
        return float(np.clip(self._dist.ppf(u), self.lo, self.hi))

    def pdf(self, x: float) -> float:
        return float(self._dist.pdf(x))

    def cdf(self, x: float) -> float:
        return float(self._dist.cdf(x))


class DiscreteDistribution:
    """A distribution over a finite set of values."""

    def __init__(self, values: list[Any], probs: list[float], label: str) -> None:
        self.values = list(values)
        p = np.asarray(probs, dtype=float)
        self.probs = p / p.sum()
        self._cum = np.cumsum(self.probs)
        self.label = label

    def sample(self, rng: np.random.Generator) -> Any:
        return self.ppf(rng.random())

    def ppf(self, u: float) -> Any:
        idx = int(np.searchsorted(self._cum, u, side="right"))
        return self.values[min(idx, len(self.values) - 1)]

    def pmf(self, value: Any) -> float:
        for v, p in zip(self.values, self.probs, strict=True):
            if v == value:
                return float(p)
        return 0.0

    pdf = pmf


def distribution_for(param: Parameter) -> NumericDistribution | DiscreteDistribution:
    """Build the nominal distribution of an ODD parameter."""
    text = param.distribution or ("bernoulli(0.5)" if param.type == "boolean" else "uniform")
    name, args = _parse(text)

    if param.type == "boolean":
        if name == "uniform" and not args:
            args = [0.5]
        if name not in ("bernoulli", "uniform") or len(args) != 1 or not 0 <= args[0] <= 1:
            raise DistributionError(f"{param.name}: boolean needs bernoulli(p), got {text!r}")
        return DiscreteDistribution([False, True], [1 - args[0], args[0]], text)

    if param.type == "categorical":
        if name != "uniform" or args:
            raise DistributionError(
                f"{param.name}: categorical supports 'uniform' (use 'weights' for bias)"
            )
        values = param.values or []
        if not values:
            raise DistributionError(f"{param.name}: categorical parameter needs 'values'")
        weights = param.weights if param.weights is not None else [1.0] * len(values)
        if len(weights) != len(values) or sum(weights) <= 0:
            raise DistributionError(f"{param.name}: 'weights' must match 'values' and be > 0")
        return DiscreteDistribution(values, weights, text)

    if param.range is None:
        raise DistributionError(f"{param.name}: {param.type} parameter needs 'range'")
    lo, hi = param.range
    if not lo < hi:
        raise DistributionError(f"{param.name}: range min must be < max")
    width = hi - lo

    if name == "uniform" and not args:
        frozen = stats.uniform(loc=lo, scale=width)
    elif name in ("normal", "truncnormal") and len(args) == 2 and args[1] > 0:
        mu, sigma = args
        frozen = stats.truncnorm((lo - mu) / sigma, (hi - mu) / sigma, loc=mu, scale=sigma)
    elif name == "loguniform" and not args:
        if lo <= 0:
            raise DistributionError(f"{param.name}: loguniform needs range min > 0")
        frozen = stats.loguniform(lo, hi)
    elif name == "triangular" and len(args) == 1 and lo <= args[0] <= hi:
        frozen = stats.triang(c=(args[0] - lo) / width, loc=lo, scale=width)
    elif name == "beta" and len(args) == 2 and min(args) > 0:
        frozen = stats.beta(args[0], args[1], loc=lo, scale=width)
    else:
        raise DistributionError(f"{param.name}: unsupported or malformed distribution {text!r}")
    return NumericDistribution(frozen, lo, hi, text)
