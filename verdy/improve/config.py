"""Improvement-loop configs.

A loop config points at a normal run config (ODD, specs, backend, verdict) and says how
to train. Every module is swappable: ``trainer``, ``feedback.labeler`` and
``reward_model`` accept ``module:attribute`` references to your own classes.

.. code-block:: yaml

    name: home_robot_improve
    evaluation: run.yaml              # ODD, specs, backend and verdict come from here
    policy:                           # trainable policy: factory(**params)
      factory: policy:make_policy
      params: {cruise: 1.0, slow_radius: 1.5}
    cycles: 3
    diagnose: {runs: 300, sampler: stratified, seed: 100}
    certify:  {runs: 1000, sampler: stratified, seed: 9000}   # held out, own seeds
    reward:
      safety: {weight: 1.0, margin: 0.3}
      preference: {weight: 0.5}
    feedback:
      labeler: {type: file, directory: feedback}   # file | cli | scripted | module:attr
      pairs_per_cycle: 30
    curriculum: {focus: 0.7, scenarios: 120}
    demonstrations: [demos/operator.jsonl]
    trainer:
      type: parameter_search          # parameter_search | command | module:attr
      options: {bounds: {cruise: [0.5, 1.2]}, iterations: 3}
    promote: no_worse                 # or: better
    guard: {reach_goal: 0.01}         # specs that may not regress by more than this
    output: reports/improve
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from verdy.config import ConfigError, import_object, load_run_config
from verdy.improve.curriculum import FailureFocusedCurriculum
from verdy.improve.demos import load_demonstrations
from verdy.improve.loop import ImprovementLoop, SplitConfig
from verdy.improve.preferences import (
    BradleyTerryRewardModel,
    CLILabeler,
    FileLabeler,
    ScriptedLabeler,
)
from verdy.improve.rewards import SafetyMarginReward
from verdy.improve.trainers import CommandTrainer, ParameterizedPolicy, ParameterSearchTrainer
from verdy.odd.loader import read_document
from verdy.secrets import find_credentials

_SPLIT = {
    "type": "object", "required": ["runs"], "additionalProperties": False,
    "properties": {"runs": {"type": "integer", "minimum": 1}, "sampler": {"type": "string"},
                   "seed": {"type": "integer"}, "options": {"type": "object"}},
}
_COMPONENT = {
    "type": "object", "required": ["type"],
    "properties": {"type": {"type": "string"}, "options": {"type": "object"}},
}
LOOP_SCHEMA = {
    "type": "object",
    "required": ["evaluation", "trainer"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "evaluation": {"type": "string"},
        "policy": {
            "type": "object", "required": ["factory"], "additionalProperties": False,
            "properties": {"factory": {"type": "string"}, "params": {"type": "object"}},
        },
        "cycles": {"type": "integer", "minimum": 1},
        "diagnose": _SPLIT,
        "certify": _SPLIT,
        "reward": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "safety": {"type": "object"},
                "preference": {"type": "object"},
            },
        },
        "reward_model": _COMPONENT,
        "feedback": {
            "type": "object", "additionalProperties": False,
            "properties": {"labeler": {"type": "object"},
                           "pairs_per_cycle": {"type": "integer", "minimum": 0}},
        },
        "curriculum": {
            "type": "object", "additionalProperties": False,
            "properties": {"focus": {"type": "number"}, "scenarios": {"type": "integer"},
                           "elite_fraction": {"type": "number"}, "seed": {"type": "integer"}},
        },
        "demonstrations": {"type": "array", "items": {"type": "string"}},
        "trainer": _COMPONENT,
        "promote": {"enum": ["no_worse", "better"]},
        "guard": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
        "seed": {"type": "integer"},
        "output": {"type": "string"},
    },
}


@dataclass
class LoopConfig:
    path: Path
    raw: dict[str, Any]
    loop: ImprovementLoop
    cycles: int


def _labeler(spec: dict[str, Any] | None, base: Path) -> Any:
    if not spec:
        return None
    spec = dict(spec)
    kind = spec.pop("type", "file")
    if kind == "file":
        return FileLabeler(base / spec.get("directory", "feedback"), spec.get("name", "operators"))
    if kind == "cli":
        return CLILabeler(show=spec.get("show"))
    if kind == "scripted":
        fn = import_object(spec["function"], base)
        return ScriptedLabeler(fn, spec.get("name", "scripted"))
    return import_object(kind, base)(**spec.get("options", {}))


def _trainer(spec: dict[str, Any], base: Path) -> Any:
    kind, opts = spec["type"], dict(spec.get("options") or {})
    if kind == "parameter_search":
        return ParameterSearchTrainer(**opts)
    if kind == "command":
        if "loader" not in opts:
            raise ConfigError("command trainer needs 'loader: module:function' to load the result")
        opts["loader"] = import_object(opts["loader"], base)
        opts["cwd"] = str(base / opts["cwd"]) if opts.get("cwd") else str(base)
        return CommandTrainer(**opts)
    return import_object(kind, base)(**opts)


def load_loop_config(path: str | Path, *, cycles: int | None = None) -> LoopConfig:
    path = Path(path).resolve()
    base = path.parent
    raw = read_document(path)
    leaked = find_credentials(raw)
    if leaked:
        raise ConfigError(f"{path.name} contains what looks like a credential at: "
                          f"{', '.join(leaked)}. Refer to secrets by name instead.")
    try:
        jsonschema.validate(raw, LOOP_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ConfigError(f"{path.name}: {where}: {exc.message}") from exc

    run = load_run_config(base / raw["evaluation"])
    policy = run.policy
    if raw.get("policy"):
        factory = import_object(raw["policy"]["factory"], base)
        policy = ParameterizedPolicy(factory, raw["policy"].get("params") or {})

    seed = int(raw.get("seed", 0))
    reward_cfg = raw.get("reward") or {}
    safety_cfg = dict(reward_cfg.get("safety") or {})
    pref_cfg = dict(reward_cfg.get("preference") or {})
    weights = {"safety": float(safety_cfg.pop("weight", 1.0)),
               "preference": float(pref_cfg.pop("weight", 0.5))}
    if raw.get("reward_model"):
        rm = raw["reward_model"]
        reward_model = import_object(rm["type"], base)(**(rm.get("options") or {}))
    else:
        reward_model = BradleyTerryRewardModel(**pref_cfg)

    cur = raw.get("curriculum") or {}
    curriculum = FailureFocusedCurriculum(
        run.odd, focus=cur.get("focus", 0.7), elite_fraction=cur.get("elite_fraction", 0.25),
        seed=cur.get("seed", seed + 7919),
        fail_on=[s.name for s in run.specs if s.severity in run.verdict.fail_on])
    demos = load_demonstrations([base / p for p in raw.get("demonstrations", [])])
    feedback = raw.get("feedback") or {}
    out = base / raw.get("output", f"reports/{raw.get('name', path.stem)}")

    loop = ImprovementLoop(
        run.odd, run.specs, run.backend, policy, _trainer(raw["trainer"], base), run.verdict,
        out,
        diagnose=SplitConfig(**{"runs": 200, "seed": 100, **(raw.get("diagnose") or {})}),
        certify=SplitConfig(**{"runs": 1000, "seed": 9000, **(raw.get("certify") or {})}),
        labeler=_labeler(feedback.get("labeler"), base),
        reward_model=reward_model,
        safety_reward=SafetyMarginReward(run.specs, **safety_cfg),
        weights=weights,
        curriculum=curriculum,
        demonstrations=demos,
        training_scenarios=int(cur.get("scenarios", 100)),
        pairs_per_cycle=int(feedback.get("pairs_per_cycle", 20)),
        promote=raw.get("promote", "no_worse"),
        guard=raw.get("guard"),
        seed=seed,
        inputs={"loop_config": raw},
    )
    return LoopConfig(path, raw, loop, cycles or int(raw.get("cycles", 3)))
