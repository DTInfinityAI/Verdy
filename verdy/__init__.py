"""Verdy: a driving test for robot AI.

Describe operating conditions as an ODD, sample scenarios from it, run a policy on a
backend, score every run against STL safety specs, and get a PASS / FAIL / INCONCLUSIVE
verdict with confidence bounds and a reproducible evidence report.
"""
__version__ = "0.6.0"

from verdy.harness import EvaluationResult, RunRecord, evaluate  # noqa: E402
from verdy.metrics import STLSpec, load_specs  # noqa: E402
from verdy.odd import ODD, load_odd, validate_odd  # noqa: E402
from verdy.verdict import Status, VerdictConfig  # noqa: E402

__all__ = [
    "ODD",
    "EvaluationResult",
    "RunRecord",
    "STLSpec",
    "Status",
    "VerdictConfig",
    "__version__",
    "evaluate",
    "load_odd",
    "load_specs",
    "validate_odd",
]
