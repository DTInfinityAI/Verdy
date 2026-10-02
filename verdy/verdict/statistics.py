"""Failure-probability estimates with confidence bounds."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
from scipy import stats


@dataclass
class FailureEstimate:
    """Estimated probability that a run fails, with one-sided confidence bounds.

    ``upper`` is a one-sided upper bound at ``confidence`` (the true failure probability is
    below it with that confidence); ``lower`` is the matching one-sided lower bound.
    """

    n: int
    failures: int
    estimate: float
    lower: float
    upper: float
    confidence: float
    method: str
    effective_n: float

    def to_dict(self) -> dict:
        return asdict(self)


def _beta_bounds(k: float, n: float, confidence: float) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lower = 0.0 if k <= 0 else float(stats.beta.ppf(alpha, k, n - k + 1))
    upper = 1.0 if k >= n else float(stats.beta.ppf(confidence, k + 1, n - k))
    return lower, upper


def clopper_pearson(failures: int, n: int, confidence: float = 0.95) -> FailureEstimate:
    """Exact binomial bounds for ``failures`` out of ``n`` independent runs."""
    if n <= 0:
        return FailureEstimate(0, 0, math.nan, 0.0, 1.0, confidence, "clopper-pearson", 0.0)
    lower, upper = _beta_bounds(failures, n, confidence)
    return FailureEstimate(
        n, failures, failures / n, lower, upper, confidence, "clopper-pearson", float(n)
    )


def weighted_estimate(
    failed: Sequence[bool], weights: Sequence[float], confidence: float = 0.95
) -> FailureEstimate:
    """Self-normalized importance-sampling estimate.

    Bounds are approximate: the wider of a normal interval on the weighted estimate and
    Clopper-Pearson bounds computed at the effective sample size. With all weights equal
    to 1 this reduces to Clopper-Pearson.
    """
    f = np.asarray(failed, dtype=float)
    w = np.asarray(weights, dtype=float)
    n = len(f)
    if n == 0 or w.sum() <= 0:
        return FailureEstimate(n, int(f.sum()), math.nan, 0.0, 1.0, confidence, "importance", 0.0)
    if np.allclose(w, w[0]):
        return clopper_pearson(int(f.sum()), n, confidence)
    wn = w / w.sum()
    p = float(np.sum(wn * f))
    ess = float(1.0 / np.sum(wn**2))
    se = float(np.sqrt(np.sum(wn**2 * (f - p) ** 2)))
    z = float(stats.norm.ppf(confidence))
    beta_lo, beta_hi = _beta_bounds(p * ess, ess, confidence) if ess >= 1 else (0.0, 1.0)
    lower = max(0.0, min(p - z * se, beta_lo))
    upper = min(1.0, max(p + z * se, beta_hi))
    return FailureEstimate(n, int(f.sum()), p, lower, upper, confidence, "importance", ess)


def required_runs(max_failure_prob: float, confidence: float = 0.95) -> int:
    """Failure-free runs needed for the upper bound to drop below ``max_failure_prob``."""
    if not 0 < max_failure_prob < 1:
        raise ValueError("max_failure_prob must be between 0 and 1")
    return math.ceil(math.log(1.0 - confidence) / math.log(1.0 - max_failure_prob))
