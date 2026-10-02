"""PASS / FAIL / INCONCLUSIVE decision rules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from verdy.verdict.coverage import CoverageReport
from verdy.verdict.statistics import FailureEstimate, required_runs


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class VerdictConfig:
    """What "safe enough" means for this evaluation.

    Args:
        max_failure_prob: highest acceptable probability that a run fails.
        confidence: confidence level of the bounds (one-sided).
        min_coverage: minimum overall ODD coverage required to PASS (0-1).
        min_runs: minimum number of runs before any decision other than INCONCLUSIVE.
        fail_on: spec severities that count as a failed run.
        errors_as_failures: count runs that crashed or produced bad traces as failures.
    """

    max_failure_prob: float = 0.01
    confidence: float = 0.95
    min_coverage: float = 0.0
    min_runs: int = 1
    fail_on: list[str] = field(default_factory=lambda: ["critical", "major"])
    errors_as_failures: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.max_failure_prob < 1:
            raise ValueError("max_failure_prob must be between 0 and 1")
        if not 0.5 <= self.confidence < 1:
            raise ValueError("confidence must be in [0.5, 1)")
        if not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be in [0, 1]")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerdictConfig:
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Verdict:
    status: Status
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reasons": list(self.reasons)}


def decide(
    estimate: FailureEstimate, coverage: CoverageReport | None, config: VerdictConfig
) -> Verdict:
    """Apply the decision rule.

    * FAIL when the lower bound on failure probability exceeds ``max_failure_prob``:
      the policy fails more often than allowed, with the stated confidence.
    * PASS when the upper bound is at or below ``max_failure_prob`` and coverage meets
      ``min_coverage``.
    * INCONCLUSIVE otherwise: more runs (or more coverage) are needed.
    """
    p_max, conf = config.max_failure_prob, config.confidence
    pct = f"{conf:.0%}"
    if estimate.n < config.min_runs:
        return Verdict(
            Status.INCONCLUSIVE, [f"only {estimate.n} runs; at least {config.min_runs} required"]
        )
    if estimate.lower > p_max:
        return Verdict(
            Status.FAIL,
            [
                f"failure probability is above {p_max:g} with {pct} confidence "
                f"(lower bound {estimate.lower:.4g}, "
                f"{estimate.failures} of {estimate.n} runs failed)"
            ],
        )
    cov = coverage.overall if coverage is not None else 1.0
    if estimate.upper <= p_max:
        if cov >= config.min_coverage:
            return Verdict(
                Status.PASS,
                [
                    f"failure probability is at most {estimate.upper:.4g} (<= {p_max:g}) "
                    f"with {pct} confidence"
                ],
            )
        return Verdict(
            Status.INCONCLUSIVE,
            [f"bounds are met but ODD coverage {cov:.0%} is below the required "
             f"{config.min_coverage:.0%}"],
        )
    reasons = [
        f"upper bound {estimate.upper:.4g} is above {p_max:g} and lower bound "
        f"{estimate.lower:.4g} is below it; more runs are needed"
    ]
    if estimate.failures == 0:
        reasons.append(
            f"with zero failures, about {required_runs(p_max, conf)} runs are needed to PASS"
        )
    return Verdict(Status.INCONCLUSIVE, reasons)
