"""The evaluation pipeline: ODD -> scenarios -> rollouts -> STL scores -> verdict -> ledger."""
from __future__ import annotations

import math
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from verdy import policy as policy_api
from verdy.backends.base import Backend, validate_trace
from verdy.ledger import build_report, sha256_of
from verdy.metrics.stl import STLEvaluator, STLSpec
from verdy.odd.model import ODD
from verdy.sampler.base import Sampler, Scenario
from verdy.verdict import (
    CoverageReport,
    FailureEstimate,
    Verdict,
    VerdictConfig,
    compute_coverage,
    decide,
    weighted_estimate,
)


@dataclass
class RunRecord:
    scenario: Scenario
    robustness: dict[str, float]
    violated: list[str]
    failed: bool
    error: str | None = None
    trace_sha256: str | None = None
    duration_s: float = 0.0

    @property
    def score(self) -> float:
        """Lowest robustness over the specs that decide failure (NaN on error)."""
        return min(self.robustness.values()) if self.robustness else math.nan

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scenario.to_dict(),
            "robustness": self.robustness,
            "violated": self.violated,
            "failed": self.failed,
            "error": self.error,
            "trace_sha256": self.trace_sha256,
            "duration_s": round(self.duration_s, 4),
        }


@dataclass
class EvaluationResult:
    verdict: Verdict
    estimate: FailureEstimate
    per_spec: dict[str, FailureEstimate]
    coverage: CoverageReport
    runs: list[RunRecord]
    report: dict[str, Any]
    traces: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.verdict.status.value

    def summary(self) -> str:
        e = self.estimate
        lines = [
            f"Verdict: {self.status}",
            *(f"  - {r}" for r in self.verdict.reasons),
            f"Runs: {e.n} ({e.failures} failed, "
            f"{sum(1 for r in self.runs if r.error)} errors)",
            f"Failure probability: {e.estimate:.4g} "
            f"[{e.lower:.4g}, {e.upper:.4g}] at {e.confidence:.0%} ({e.method})",
            f"ODD coverage: {self.coverage.overall:.0%}"
            + (f", pairwise {self.coverage.pairwise:.0%}" if self.coverage.pairwise is not None
               else ""),
            "Per spec:",
        ]
        for name, est in self.per_spec.items():
            lines.append(
                f"  {name:<24} {est.failures:>5} violations  p={est.estimate:.4g} "
                f"(upper {est.upper:.4g})"
            )
        lines.append(f"Report digest: {self.report['integrity']['body_sha256']}")
        return "\n".join(lines)


def evaluate(
    odd: ODD,
    specs: list[STLSpec],
    backend: Backend,
    policy: Any,
    sampler: Sampler,
    n_runs: int,
    verdict: VerdictConfig | None = None,
    *,
    batch_size: int | None = None,
    keep_traces: bool | str = False,
    inputs: dict[str, Any] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> EvaluationResult:
    """Run a full evaluation and return the verdict with its evidence report.

    Args:
        n_runs: number of scenarios to run (fewer if a replay sampler runs out of logs).
        batch_size: scenarios per batch; adaptive samplers are updated after each batch.
            Defaults to 50 for adaptive samplers and ``n_runs`` otherwise.
        keep_traces: ``True`` keeps every trace in the result, ``"failures"`` keeps only
            traces of failed runs.
        inputs: extra entries recorded under ``inputs`` in the report (e.g. config files).
        progress: called with ``(done, total)`` after each run.
    """
    config = verdict or VerdictConfig()
    evaluator = STLEvaluator(specs)
    deciding = [s.name for s in specs if s.severity in config.fail_on]
    if not deciding:
        raise ValueError(f"no spec has a severity in fail_on={config.fail_on}")
    if batch_size is None:
        batch_size = 50 if sampler.adaptive else max(n_runs, 1)

    backend.bind(odd)
    runs: list[RunRecord] = []
    traces: dict[str, dict[str, list[float]]] = {}
    try:
        while len(runs) < n_runs:
            batch = sampler.sample(min(batch_size, n_runs - len(runs)))
            if not batch:
                break
            batch_runs = []
            for scenario in batch:
                record, trace = _run_one(scenario, backend, policy, evaluator, deciding, config)
                batch_runs.append(record)
                if trace is not None and (
                    keep_traces is True or (keep_traces == "failures" and record.failed)
                ):
                    traces[scenario.id] = trace
                if progress:
                    progress(len(runs) + len(batch_runs), n_runs)
            runs.extend(batch_runs)
            if sampler.adaptive:
                sampler.update(
                    [r.scenario for r in batch_runs],
                    [min((r.robustness[n] for n in deciding), default=math.nan)
                     if not r.error else math.nan for r in batch_runs],
                )
    finally:
        backend.close()

    weights = [r.scenario.weight for r in runs]
    estimate = weighted_estimate([r.failed for r in runs], weights, config.confidence)
    per_spec = {
        s.name: weighted_estimate(
            [s.name in r.violated for r in runs], weights, config.confidence
        )
        for s in specs
    }
    coverage = compute_coverage(odd, [r.scenario.params for r in runs])
    result_verdict = decide(estimate, coverage, config)

    report_inputs = {
        "odd": odd.to_dict(),
        "odd_sha256": sha256_of(odd.to_dict()),
        "specs": [s.to_dict() for s in specs],
        "specs_sha256": sha256_of([s.to_dict() for s in specs]),
        "policy": policy_api.describe(policy),
        "backend": backend.name,
        "sampler": sampler.config(),
        "n_runs_requested": n_runs,
        "batch_size": batch_size,
        "verdict_config": config.to_dict(),
        **(inputs or {}),
    }
    results = {
        "verdict": result_verdict.to_dict(),
        "failure_probability": estimate.to_dict(),
        "per_spec": {k: v.to_dict() for k, v in per_spec.items()},
        "coverage": coverage.to_dict(),
        "n_runs": len(runs),
        "n_errors": sum(1 for r in runs if r.error),
    }
    report = build_report(inputs=report_inputs, runs=[r.to_dict() for r in runs], results=results)
    return EvaluationResult(result_verdict, estimate, per_spec, coverage, runs, report, traces)


def _run_one(
    scenario: Scenario,
    backend: Backend,
    policy: Any,
    evaluator: STLEvaluator,
    deciding: list[str],
    config: VerdictConfig,
) -> tuple[RunRecord, dict[str, list[float]] | None]:
    start = time.perf_counter()
    try:
        env = backend.build(scenario.to_dict())
        trace = validate_trace(backend.rollout(env, policy, scenario.seed), evaluator.signals)
        robustness = evaluator.robustness(trace)
    except Exception as exc:  # a crashing run is evidence too: record it, don't abort
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return (
            RunRecord(
                scenario, {}, [], config.errors_as_failures, error=detail,
                duration_s=time.perf_counter() - start,
            ),
            None,
        )
    violated = [name for name, r in robustness.items() if not r >= 0]  # NaN counts as violated
    failed = any(name in violated for name in deciding)
    record = RunRecord(
        scenario, robustness, violated, failed, trace_sha256=sha256_of(trace),
        duration_s=time.perf_counter() - start,
    )
    return record, trace
