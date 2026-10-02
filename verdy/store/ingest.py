"""Turn evidence reports into index rows.

Pure functions, independent of the database, so any :class:`~verdy.store.base.Store`
implementation can reuse them. Report format ``1`` is supported; reports of an unknown
format are skipped (and reported), never misread.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from verdy.ledger import sha256_of

SUPPORTED_REPORT_VERSIONS = ("1",)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, str):  # non-finite numbers are stored as strings
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def policy_identity(inputs: dict[str, Any]) -> tuple[str, str]:
    """``(name, version)`` of the policy a report evaluated.

    Uses ``inputs.policy_name`` and ``inputs.policy_version`` when the run set them
    (``policy_name`` / ``policy_version`` in the run config, or ``--policy-version``).
    Otherwise the name is the policy reference from the run config (or the class), and
    the version is a short hash of the run config's policy section, so different tunings
    of the same code count as different versions.
    """
    config = inputs.get("config") or {}
    pol = config.get("policy")
    if isinstance(pol, dict):
        ref = pol.get("factory", "")
    else:
        ref = pol or ""
    name = inputs.get("policy_name") or ref or inputs.get("policy") or "unknown"
    version = inputs.get("policy_version")
    if not version:
        basis = pol if pol is not None else inputs.get("policy")
        version = "cfg-" + sha256_of(basis)[:10]
    return str(name), str(version)


def suite_identity(inputs: dict[str, Any], odd_key: str) -> tuple[str, str]:
    """``(key, label)`` of the test a report ran: ODD, specs and verdict rule.

    Two verdicts are only comparable when their suite keys match; the policy under test,
    the sampler, seed and number of runs may differ.
    """
    specs = inputs.get("specs") or []
    specs_key = inputs.get("specs_sha256") or sha256_of(specs)
    v = inputs.get("verdict_config") or {}
    rule = {k: v.get(k) for k in ("max_failure_prob", "confidence", "fail_on",
                                  "min_coverage", "errors_as_failures")}
    odd = inputs.get("odd") or {}
    fail_on = "+".join(v.get("fail_on") or []) or "all"
    label = (f"{odd.get('name', '?')} {odd.get('version', '?')} | {len(specs)} specs, "
             f"{fail_on} | p <= {v.get('max_failure_prob')} @ {v.get('confidence')}")
    return sha256_of([odd_key, specs_key, rule]), label


def report_rows(report: dict[str, Any], path: str) -> dict[str, list[dict[str, Any]]]:
    """Rows for every table, keyed by table name, for one verified report."""
    digest = report["integrity"]["body_sha256"]
    inputs = report.get("inputs") or {}
    results = report.get("results") or {}
    kind = "improvement_loop" if inputs.get("kind") == "improvement_loop" else "evaluation"
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in (
        "dim_odd", "dim_policy", "dim_spec", "dim_backend", "dim_scenario",
        "fact_verdict", "fact_rollout", "fact_rollout_spec", "fact_feedback")}
    signature = (report["integrity"].get("signature") or {}).get("method")
    rows["sources"] = [{
        "report_digest": digest, "report_path": str(path), "kind": kind,
        "report_version": report.get("report_version"), "valid": True,
        "signature_method": signature, "indexed_at": datetime.now(),
    }]
    if kind == "improvement_loop":
        prefs = Path(path).parent / "preferences.jsonl"
        if prefs.is_file():
            for line in prefs.read_text("utf-8").splitlines():
                if line.strip():
                    p = json.loads(line)
                    rows["fact_feedback"].append({
                        "pair_id": p["pair_id"], "source_digest": digest, "run_a": p["a"],
                        "run_b": p["b"], "choice": p["choice"], "labeler": p.get("labeler"),
                        "reason": p.get("reason", "")})
        return rows

    odd = inputs.get("odd") or {}
    odd_key = inputs.get("odd_sha256") or sha256_of(odd)
    rows["dim_odd"].append({"odd_key": odd_key, "name": odd.get("name"),
                            "version": odd.get("version"),
                            "n_parameters": len(odd.get("parameters") or [])})
    name, version = policy_identity(inputs)
    policy_key = sha256_of([name, version])
    rows["dim_policy"].append({"policy_key": policy_key, "name": name, "version": version,
                               "reference": inputs.get("policy")})
    backend_cfg = inputs.get("backend_config") or {}
    backend_key = sha256_of([inputs.get("backend"), backend_cfg])
    rows["dim_backend"].append({"backend_key": backend_key, "name": inputs.get("backend"),
                                "config_json": json.dumps(backend_cfg, sort_keys=True)})
    spec_keys = {}
    for spec in inputs.get("specs") or []:
        key = sha256_of(spec)
        spec_keys[spec["name"]] = key
        rows["dim_spec"].append({"spec_key": key, "name": spec["name"],
                                 "formula": spec.get("formula"),
                                 "severity": spec.get("severity")})

    est = results.get("failure_probability") or {}
    cov = results.get("coverage") or {}
    sampler = inputs.get("sampler") or {}
    vcfg = inputs.get("verdict_config") or {}
    suite_key, suite_label = suite_identity(inputs, odd_key)
    rows["fact_verdict"].append({
        "report_digest": digest, "created_at": _ts(report.get("created_at")),
        "verdy_version": report.get("verdy_version"), "suite_key": suite_key,
        "suite_label": suite_label, "odd_key": odd_key,
        "policy_key": policy_key, "backend_key": backend_key,
        "sampler": sampler.get("sampler"), "seed": sampler.get("seed"),
        "status": (results.get("verdict") or {}).get("status"),
        "n_runs": results.get("n_runs"), "n_errors": results.get("n_errors"),
        "failures": est.get("failures"), "p_fail": _num(est.get("estimate")),
        "p_lower": _num(est.get("lower")), "p_upper": _num(est.get("upper")),
        "confidence": _num(est.get("confidence")),
        "max_failure_prob": _num(vcfg.get("max_failure_prob")), "method": est.get("method"),
        "coverage": _num(cov.get("overall")), "pairwise_coverage": _num(cov.get("pairwise")),
    })

    for run in report.get("runs") or []:
        scen_key = sha256_of({"params": run.get("params"), "seed": run.get("seed")})
        rows["dim_scenario"].append({"scenario_key": scen_key,
                                     "params_json": json.dumps(run.get("params"), sort_keys=True),
                                     "seed": run.get("seed")})
        rob = run.get("robustness") or {}
        finite = [v for v in (_num(x) for x in rob.values()) if v is not None]
        rows["fact_rollout"].append({
            "report_digest": digest, "run_id": run["id"], "scenario_key": scen_key,
            "failed": bool(run.get("failed")), "errored": bool(run.get("error")),
            "weight": _num(run.get("weight")), "duration_s": _num(run.get("duration_s")),
            "min_robustness": min(finite) if finite else None,
            "trace_sha256": run.get("trace_sha256"),
        })
        violated = set(run.get("violated") or [])
        for spec_name, value in rob.items():
            rows["fact_rollout_spec"].append({
                "report_digest": digest, "run_id": run["id"],
                "spec_key": spec_keys.get(spec_name, sha256_of({"name": spec_name})),
                "robustness": _num(value), "violated": spec_name in violated})
    return rows
