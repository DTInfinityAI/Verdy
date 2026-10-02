"""The improvement loop: test, find weaknesses, train on them, re-certify.

Each cycle:

1. **Diagnose.** Evaluate the current policy on a fresh diagnosis sample of the ODD.
2. **Reward.** Score every run with the reward: safety margins from STL robustness, plus
   the learned preference reward.
3. **Feedback.** Ask the labeler (operators) to compare informative pairs of runs, and
   refit the preference reward model on everything collected so far.
4. **Target.** Build a failure map and a training curriculum concentrated where the
   policy failed or nearly failed.
5. **Train.** Hand everything to the trainer, which returns a candidate policy.
6. **Re-certify.** Evaluate the candidate *and* the incumbent on a fresh certification
   sample with its own seed stream, which neither diagnosis nor training ever uses, and
   get an independent verdict. The candidate replaces the incumbent only if it does at
   least as well (``promote``), so improvement is proven, not assumed.

Before the first cycle, demonstrations (if any) pretrain the policy, and the starting
policy gets a baseline certification. Every evaluation writes a normal Verdy evidence
report, and the loop writes a sealed loop report linking them all.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from verdy import policy as policy_api
from verdy.backends.base import Backend
from verdy.harness import EvaluationResult, evaluate
from verdy.improve.curriculum import FailureFocusedCurriculum, failure_map
from verdy.improve.episodes import Episode, episodes_from
from verdy.improve.preferences import (
    BradleyTerryRewardModel,
    Labeler,
    Preference,
    load_preferences,
    save_preferences,
    select_pairs,
)
from verdy.improve.rewards import CompositeReward, PreferenceReward, SafetyMarginReward
from verdy.improve.trainers import TrainingContext, TrainingData
from verdy.ledger import build_report, write_report
from verdy.metrics.stl import STLSpec
from verdy.odd.model import ODD
from verdy.sampler import make_sampler
from verdy.verdict import VerdictConfig

STATUS_RANK = {"FAIL": 0, "INCONCLUSIVE": 1, "PASS": 2}


@dataclass
class SplitConfig:
    """How one evaluation split is sampled. Each split has its own seed stream."""

    runs: int
    sampler: str = "stratified"
    seed: int = 0
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class CertificationResult:
    status: str
    failure_probability: float
    upper: float
    lower: float
    runs: int
    report_path: str
    report_digest: str
    per_spec: dict[str, float] = field(default_factory=dict)
    """Violation rate of every spec, including ones that do not decide the verdict."""

    @classmethod
    def from_result(cls, result: EvaluationResult, path: Path) -> CertificationResult:
        e = result.estimate
        return cls(result.status, e.estimate, e.upper, e.lower, e.n, str(path),
                   result.report["integrity"]["body_sha256"],
                   {k: v.estimate for k, v in result.per_spec.items()})


@dataclass
class CycleResult:
    cycle: int
    diagnosis: dict[str, Any]
    feedback: dict[str, Any]
    training: dict[str, Any]
    candidate: CertificationResult
    incumbent: CertificationResult
    promoted: bool
    reason: str
    policy: str

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["candidate"] = self.candidate.__dict__
        out["incumbent"] = self.incumbent.__dict__
        return out


@dataclass
class LoopResult:
    policy: Any
    baseline: CertificationResult
    cycles: list[CycleResult]
    report: dict[str, Any]
    report_path: Path
    before_pretraining: CertificationResult | None = None

    @property
    def final(self) -> CertificationResult:
        return _final(self.baseline, self.cycles)

    def summary(self) -> str:
        lines = []
        if self.before_pretraining:
            p = self.before_pretraining
            lines.append(f"Starting policy: {p.status}  p_fail={p.failure_probability:.4g} "
                         f"[{p.lower:.4g}, {p.upper:.4g}]")
        b = self.baseline
        label = "After pretraining on demonstrations" if self.before_pretraining else "Baseline"
        lines.append(f"{label}: {b.status}  p_fail={b.failure_probability:.4g} "
                     f"[{b.lower:.4g}, {b.upper:.4g}]")
        for c in self.cycles:
            k = c.candidate
            mark = "promoted" if c.promoted else "kept incumbent"
            lines.append(
                f"Cycle {c.cycle}: candidate {k.status}  p_fail={k.failure_probability:.4g} "
                f"[{k.lower:.4g}, {k.upper:.4g}] vs incumbent "
                f"{c.incumbent.failure_probability:.4g} -> {mark} ({c.reason})")
        f = self.final
        lines.append(f"Final certified policy: {f.status}  p_fail={f.failure_probability:.4g} "
                     f"(upper {f.upper:.4g})")
        lines.append("  per-spec violation rates: " + ", ".join(
            f"{k} {v:.3g}" for k, v in f.per_spec.items()))
        lines.append(f"Loop report: {self.report_path} "
                     f"(digest {self.report['integrity']['body_sha256'][:16]}...)")
        return "\n".join(lines)


def _final(baseline: CertificationResult, cycles: Sequence[CycleResult]) -> CertificationResult:
    """The current policy's certification on the most recent held-out sample."""
    if not cycles:
        return baseline
    last = cycles[-1]
    return last.candidate if last.promoted else last.incumbent


def _key(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


def _promote(candidate: CertificationResult, incumbent: CertificationResult, rule: str,
             guard: dict[str, float] | None = None) -> tuple[bool, str]:
    rc, ri = STATUS_RANK[candidate.status], STATUS_RANK[incumbent.status]
    for spec, tolerance in (guard or {}).items():
        new, old = candidate.per_spec.get(spec), incumbent.per_spec.get(spec)
        if new is not None and old is not None and new > old + tolerance:
            return False, (f"{spec} regressed from {old:.3g} to {new:.3g} "
                           f"(tolerance {tolerance:g})")
    if rc < ri:
        return False, f"verdict {candidate.status} is worse than {incumbent.status}"
    if rule == "better" and not candidate.failure_probability < incumbent.failure_probability:
        return False, "failure probability did not improve"
    if candidate.failure_probability > incumbent.failure_probability:
        return False, "failure probability got worse on held-out scenarios"
    if rc > ri:
        return True, f"verdict improved to {candidate.status}"
    return True, "failure probability on held-out scenarios is no worse"


class ImprovementLoop:
    """Closed-loop policy improvement with independent re-certification.

    Args:
        trainer: any object with ``train(policy, data, ctx)`` (see
            :mod:`verdy.improve.trainers`), optionally ``pretrain``.
        labeler: source of human preferences, or ``None`` to skip feedback.
        reward_model: preference model; default :class:`BradleyTerryRewardModel`.
        safety_reward: default :class:`SafetyMarginReward` over the specs.
        weights: ``{"safety": w, "preference": w}`` for the combined reward.
        curriculum: default :class:`FailureFocusedCurriculum`.
        demonstrations: expert steps for ``trainer.pretrain``.
        diagnose, certify: how the two splits are sampled. Their seeds must differ.
        training_scenarios: curriculum size per cycle.
        pairs_per_cycle: pairs sent to the labeler per cycle.
        promote: ``"no_worse"`` (default) or ``"better"``.
        guard: ``{spec: tolerance}``: specs whose violation rate may not rise by more than
            ``tolerance`` for a candidate to be promoted. Use it for specs that do not
            decide the verdict, such as task completion, so safety gains are not bought by
            making the robot useless.
    """

    def __init__(
        self,
        odd: ODD,
        specs: list[STLSpec],
        backend: Backend,
        policy: Any,
        trainer: Any,
        verdict: VerdictConfig,
        output_dir: str | Path,
        *,
        diagnose: SplitConfig,
        certify: SplitConfig,
        labeler: Labeler | None = None,
        reward_model: Any = None,
        safety_reward: Any = None,
        weights: dict[str, float] | None = None,
        curriculum: Any = None,
        demonstrations: Sequence[dict[str, Any]] | None = None,
        training_scenarios: int = 100,
        pairs_per_cycle: int = 20,
        promote: str = "no_worse",
        guard: dict[str, float] | None = None,
        seed: int = 0,
        progress: Callable[[str], None] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> None:
        if diagnose.seed == certify.seed:
            raise ValueError("diagnosis and certification must use different seeds")
        if promote not in ("no_worse", "better"):
            raise ValueError("promote must be 'no_worse' or 'better'")
        self.odd, self.specs, self.backend = odd, specs, backend
        self.policy = policy
        self.trainer = trainer
        self.verdict = verdict
        self.out = Path(output_dir)
        self.diagnose_cfg, self.certify_cfg = diagnose, certify
        self.labeler = labeler
        self.reward_model = reward_model or BradleyTerryRewardModel()
        self.safety = safety_reward or SafetyMarginReward(specs)
        self.weights = {"safety": 1.0, "preference": 0.5, **(weights or {})}
        self.curriculum = curriculum or FailureFocusedCurriculum(
            odd, seed=seed + 7919, fail_on=[s.name for s in specs
                                            if s.severity in verdict.fail_on])
        self.demonstrations = list(demonstrations or [])
        self.training_scenarios = training_scenarios
        self.pairs_per_cycle = pairs_per_cycle
        self.promote = promote
        self.guard = dict(guard or {})
        unknown = set(self.guard) - {s.name for s in specs}
        if unknown:
            raise ValueError(f"guard names unknown specs: {', '.join(sorted(unknown))}")
        self.rng = np.random.default_rng(seed)
        self.progress = progress or (lambda msg: None)
        self.inputs = inputs or {}
        self.preferences: list[Preference] = load_preferences(self.out / "preferences.jsonl")
        self.ctx_reward: Callable[[Episode], float] = self.safety

    # -- helpers ---------------------------------------------------------------------

    def _evaluate(self, policy: Any, split: SplitConfig, seed: int, path: Path,
                  keep_traces: bool = False) -> EvaluationResult:
        sampler = make_sampler(split.sampler, self.odd, seed=seed, **split.options)
        result = evaluate(self.odd, self.specs, self.backend, policy, sampler, split.runs,
                          self.verdict, keep_traces=keep_traces,
                          inputs={"improvement_loop": {"split": path.stem, "seed": seed},
                                  **self.inputs})
        write_report(result.report, path)
        return result

    def _reward(self, reference: Sequence[Episode] = ()) -> CompositeReward:
        parts: list[tuple[Any, float]] = [(self.safety, self.weights["safety"])]
        if getattr(self.reward_model, "fitted", False):
            pref = PreferenceReward(self.reward_model).calibrate(reference)
            parts.append((pref, self.weights["preference"]))
        return CompositeReward(parts)

    def _describe_policy(self, policy: Any) -> str:
        params = getattr(policy, "params", None)
        return f"{policy_api.describe(policy)} {params}" if params else policy_api.describe(policy)

    # -- the loop --------------------------------------------------------------------

    def run(self, cycles: int) -> LoopResult:
        self.out.mkdir(parents=True, exist_ok=True)
        ctx = TrainingContext(self.odd, self.specs, self.backend, self.safety,
                              self.out / "training")
        pretrain_info: dict[str, Any] = {}
        start_policy = self.policy
        before_pretraining: CertificationResult | None = None
        if self.demonstrations and hasattr(self.trainer, "pretrain"):
            self.progress("certifying the starting policy before pretraining")
            path = self.out / "cycle_0" / "certification_before_pretraining.report.json"
            before_pretraining = CertificationResult.from_result(
                self._evaluate(self.policy, self.certify_cfg, self.certify_cfg.seed, path), path)
            self.progress(f"pretraining on {len(self.demonstrations)} demonstration steps")
            self.policy = self.trainer.pretrain(self.policy, self.demonstrations, ctx)
            pretrain_info = dict(getattr(self.trainer, "last_pretrain", {}) or {})
            pretrain_info["policy"] = self._describe_policy(self.policy)

        self.progress("certifying the starting policy" + (" after pretraining"
                                                          if before_pretraining else ""))
        baseline_res = self._evaluate(self.policy, self.certify_cfg, self.certify_cfg.seed,
                                      self.out / "cycle_0" / "certification.report.json")
        baseline = CertificationResult.from_result(
            baseline_res, self.out / "cycle_0" / "certification.report.json")

        results: list[CycleResult] = []
        for c in range(1, cycles + 1):
            cdir = self.out / f"cycle_{c}"
            self.progress(f"cycle {c}: diagnosing")
            diag = self._evaluate(self.policy, self.diagnose_cfg, self.diagnose_cfg.seed + c,
                                  cdir / "diagnosis.report.json", keep_traces=True)
            episodes = episodes_from(diag)

            self.progress(f"cycle {c}: collecting feedback")
            fb: dict[str, Any] = {"pairs_asked": 0, "labels_received": 0}
            if self.labeler is not None and self.pairs_per_cycle:
                pairs = select_pairs(episodes, self.pairs_per_cycle, self.rng, self.safety,
                                     self.reward_model)
                new = self.labeler.label(pairs, tag=f"c{c}-")
                known = {p.pair_id for p in self.preferences}
                self.preferences += [p for p in new if p.pair_id not in known]
                save_preferences(self.preferences, self.out / "preferences.jsonl")
                self.reward_model.fit(self.preferences)
                fb = {"pairs_asked": len(pairs), "labels_received": len(new),
                      "total_preferences": len(self.preferences),
                      "reward_model": self.reward_model.to_dict()
                      if hasattr(self.reward_model, "to_dict") else {}}
            reward = self._reward(episodes)
            ctx.reward = reward
            rewards = {e.id: reward(e) for e in episodes}

            self.progress(f"cycle {c}: building the curriculum")
            fmap = failure_map(self.odd, episodes)
            self.curriculum.fit(episodes)
            train = self.curriculum.sample(self.training_scenarios, prefix=f"c{c}-")

            # Certification scenarios come from their own seed stream; drop any training
            # scenario that happens to coincide with one, so certification stays unseen.
            cert_seed = self.certify_cfg.seed + 1000 * c
            cert_keys = {_key(s.params) for s in make_sampler(
                self.certify_cfg.sampler, self.odd, seed=cert_seed,
                **self.certify_cfg.options).sample(self.certify_cfg.runs)}
            overlap = [s for s in train if _key(s.params) in cert_keys]
            train = [s for s in train if _key(s.params) not in cert_keys]

            self.progress(f"cycle {c}: training on {len(train)} scenarios")
            data = TrainingData(c, episodes, rewards, list(self.preferences), self.reward_model,
                                train, self.demonstrations, fmap)
            before_rollouts = ctx.rollouts
            candidate = self.trainer.train(self.policy, data, ctx)
            training = {
                "trainer": self.trainer.config() if hasattr(self.trainer, "config")
                else type(self.trainer).__name__,
                "scenarios": len(train), "removed_overlap_with_certification": len(overlap),
                "rollouts": ctx.rollouts - before_rollouts,
                "mean_diagnosis_reward":
                    float(np.mean(list(rewards.values()))) if rewards else None,
                **(getattr(self.trainer, "last", None) or {}),
            }

            self.progress(f"cycle {c}: re-certifying on held-out scenarios")
            cand_res = self._evaluate(candidate, self.certify_cfg, cert_seed,
                                      cdir / "certification_candidate.report.json")
            inc_res = self._evaluate(self.policy, self.certify_cfg, cert_seed,
                                     cdir / "certification_incumbent.report.json")
            cand = CertificationResult.from_result(
                cand_res, cdir / "certification_candidate.report.json")
            inc = CertificationResult.from_result(
                inc_res, cdir / "certification_incumbent.report.json")
            promoted, reason = _promote(cand, inc, self.promote, self.guard)
            if promoted:
                self.policy = candidate
            results.append(CycleResult(
                cycle=c,
                diagnosis={"status": diag.status, "failure_probability": diag.estimate.estimate,
                           "failures": diag.estimate.failures, "runs": diag.estimate.n,
                           "weakest": fmap["weakest"][:5],
                           "report": str(cdir / "diagnosis.report.json")},
                feedback=fb, training=training, candidate=cand, incumbent=inc,
                promoted=promoted, reason=reason, policy=self._describe_policy(self.policy),
            ))

        report = build_report(
            inputs={
                "kind": "improvement_loop",
                "starting_policy": self._describe_policy(start_policy),
                "pretraining": pretrain_info,
                "reward": (ctx.reward.config() if hasattr(ctx.reward, "config")
                           else self._reward().config()),
                "curriculum": self.curriculum.config()
                if hasattr(self.curriculum, "config") else {},
                "diagnose": self.diagnose_cfg.__dict__, "certify": self.certify_cfg.__dict__,
                "verdict_config": self.verdict.to_dict(), "promote": self.promote,
                "guard": self.guard,
                "labeler": getattr(self.labeler, "name", None), **self.inputs,
            },
            runs=[r.to_dict() for r in results],
            results={"before_pretraining": before_pretraining.__dict__
                     if before_pretraining else None,
                     "baseline": baseline.__dict__,
                     "final": _final(baseline, results).__dict__,
                     "final_policy": self._describe_policy(self.policy),
                     "cycles": len(results)},
        )
        path = write_report(report, self.out / "loop.report.json")
        return LoopResult(self.policy, baseline, results, report, path, before_pretraining)
