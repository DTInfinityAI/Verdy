"""Failure-probability bounds, coverage, and PASS/FAIL/INCONCLUSIVE rules."""
from verdy.verdict.coverage import CoverageReport, compute_coverage
from verdy.verdict.decision import Status, Verdict, VerdictConfig, decide
from verdy.verdict.statistics import (
    FailureEstimate,
    clopper_pearson,
    required_runs,
    weighted_estimate,
)

__all__ = [
    "CoverageReport",
    "FailureEstimate",
    "Status",
    "Verdict",
    "VerdictConfig",
    "clopper_pearson",
    "compute_coverage",
    "decide",
    "required_runs",
    "weighted_estimate",
]
