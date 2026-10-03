"""Score a Laya checkpoint on held-out approvals, per tree level, and gate its promotion.

A checkpoint is promoted only if, on the same held-out human approvals, it

- has higher overall accuracy than the current checkpoint,
- loses no more than ``tolerance`` accuracy at any level of the tree, and
- is at least as well calibrated (expected calibration error no higher).

Calibration matters as much as accuracy here: the walker multiplies probabilities along the
path and compares the product with ``min_probability``, so an overconfident checkpoint
accepts wrong matches.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from verdy.odd.levels import QUESTION_ID

REPORT_FORMAT = "verdy-laya-eval/1"
ECE_BINS = 15

Predict = Callable[[Any, dict[str, Any]], dict[str, Any]]


def rows_sha256(rows: Sequence[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode() + b"\n")
    return h.hexdigest()


def ece(confidences: Sequence[float], corrects: Sequence[bool], bins: int = ECE_BINS) -> float:
    """Expected calibration error with equal-width confidence bins."""
    if not confidences:
        return 0.0
    totals: dict[int, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for c, ok in zip(confidences, corrects, strict=True):
        b = min(int(c * bins), bins - 1)
        totals[b][0] += 1
        totals[b][1] += c
        totals[b][2] += float(ok)
    n = len(confidences)
    return sum(cnt / n * abs(acc / cnt - conf / cnt) for cnt, conf, acc in totals.values())


def _summary(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    conf = [c["confidence"] for c in cases]
    ok = [c["correct"] for c in cases]
    n = len(cases)
    return {
        "n": n,
        "accuracy": round(sum(ok) / n, 4) if n else None,
        "ece": round(ece(conf, ok), 4) if n else None,
        "brier": round(sum((c - float(o)) ** 2 for c, o in zip(conf, ok, strict=True)) / n, 4)
        if n else None,
    }


def evaluate_rows(predict: Predict, rows: Sequence[dict[str, Any]], *,
                  checkpoint: str = "") -> dict[str, Any]:
    """Run ``predict(state, questions)`` (Laya's contract) on held-out rows and score it."""
    cases = []
    for row in rows:
        result = predict(row["state"], row["questions"])
        answer = result["answers"][QUESTION_ID]
        choice = answer["choice"]
        probs = answer.get("probabilities") or {}
        confidence = float(probs.get(choice, answer.get("answer_confidence", 0.0)))
        tags = dict(t.split(":", 1) for t in row.get("tags", []) if ":" in t)
        cases.append({"correct": choice == row["expected"][QUESTION_ID],
                      "confidence": confidence, "level": tags.get("level", "?"),
                      "kind": tags.get("kind", "?")})
    by_level: dict[str, list] = defaultdict(list)
    by_kind: dict[str, list] = defaultdict(list)
    for c in cases:
        by_level[c["level"]].append(c)
        by_kind[c["kind"]].append(c)
    return {
        "format": REPORT_FORMAT,
        "checkpoint": checkpoint,
        "dataset_sha256": rows_sha256(rows),
        "overall": _summary(cases),
        "by_level": {k: _summary(v) for k, v in sorted(by_level.items())},
        "by_kind": {k: _summary(v) for k, v in sorted(by_kind.items())},
    }


def compare_reports(candidate: dict[str, Any], baseline: dict[str, Any], *,
                    tolerance: float = 0.0) -> tuple[bool, list[str]]:
    """Whether ``candidate`` should replace ``baseline``, with the reasons."""
    if candidate.get("dataset_sha256") != baseline.get("dataset_sha256"):
        return False, ["the reports were made on different held-out data; re-run both"]
    reasons = []
    ok = True
    c, b = candidate["overall"], baseline["overall"]
    if not c["n"]:
        return False, ["no held-out examples"]
    if c["accuracy"] > b["accuracy"]:
        reasons.append(f"accuracy {b['accuracy']:.3f} -> {c['accuracy']:.3f}")
    else:
        ok = False
        reasons.append(f"accuracy not better: {b['accuracy']:.3f} -> {c['accuracy']:.3f}")
    if c["ece"] <= b["ece"]:
        reasons.append(f"ECE {b['ece']:.3f} -> {c['ece']:.3f}")
    else:
        ok = False
        reasons.append(f"calibration worse: ECE {b['ece']:.3f} -> {c['ece']:.3f}")
    for level, bl in baseline["by_level"].items():
        cl = candidate["by_level"].get(level)
        if cl is None or not bl["n"]:
            continue
        if cl["accuracy"] < bl["accuracy"] - tolerance:
            ok = False
            reasons.append(f"level {level} regressed: {bl['accuracy']:.3f} -> "
                           f"{cl['accuracy']:.3f}")
    return ok, reasons
