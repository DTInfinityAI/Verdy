"""Per-level training and evaluation data for the Laya tree walker.

One approval of a leaf at depth *d* gives *d* examples, one per level of the walk, and at
each level the siblings of the right branch are the hard negatives. Examples are generated
from the **current** tree, so restructuring the ontology regenerates them; nothing in the
log is invalidated.

- ``match`` approvals: the right child at every level from the roots to the leaf.
- ``new`` approvals: the right child at every level down to the group the entry was placed
  in, then ``none`` there, over the options snapshotted when it was approved. If the entry has
  since joined the tree, its full path to the leaf is added too.

Records are split into train and held-out sets by a stable hash of their group (a human
approval and its synthetic paraphrases share one), so a record never changes sides as the log
grows, and no paraphrase of a held-out phrase is trained on. The held-out set holds human
approvals only.

Output files:

``train.jsonl``
    Laya's typed-decisions row format: ``state``, ``questions`` and ``gold`` as JSON strings,
    gold as one-hot ``probabilities``. Option order is shuffled per row, ``none`` included,
    so the model learns content rather than position.
``heldout.jsonl``
    ``laya-evals`` format (``state``, ``questions``, ``expected``, ``tags``) in the walker's
    canonical option order, tagged ``level:N`` and ``kind:match|none``.
``manifest.json``
    Counts by level, kind and source, the ontology and log digests, and skipped records.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verdy.odd.approvals import Approval
from verdy.odd.levels import QUESTION_ID, level_options, level_question
from verdy.odd.ontology import NONE_LABEL, Ontology


@dataclass
class LevelExample:
    approval: Approval
    level: int                  # 1 = choosing among the roots
    node: str | None            # node whose children are the options (None = roots)
    options: dict[str, str]     # {id: gloss}, canonical order, without "none"
    gold: str                   # a child id or "none"

    @property
    def kind(self) -> str:
        return "none" if self.gold == NONE_LABEL else "match"

    def question(self, order: Sequence[str] | None = None) -> dict[str, Any]:
        q = level_question(self.options)
        if order is not None:
            q["criteria"] = {k: q["criteria"][k] for k in order}
        return q

    def train_row(self, rng: random.Random | None = None) -> dict[str, Any]:
        labels = [*self.options, NONE_LABEL]
        if rng is not None:
            rng.shuffle(labels)
        return {
            "state": json.dumps(self.approval.state, ensure_ascii=False),
            "questions": json.dumps({QUESTION_ID: self.question(labels)}, ensure_ascii=False),
            "gold": json.dumps({QUESTION_ID: {"probabilities": {self.gold: 1.0}}}),
            "level": self.level, "kind": self.kind, "source": self.approval.source,
            "approval": self.approval.id,
        }

    def eval_row(self) -> dict[str, Any]:
        return {
            "state": self.approval.state,
            "questions": {QUESTION_ID: self.question()},
            "expected": {QUESTION_ID: self.gold},
            "tags": [f"level:{self.level}", f"kind:{self.kind}"],
            "approval": self.approval.id,
        }


def _walk(ontology: Ontology, approval: Approval, target: str) -> list[LevelExample]:
    """The right child at each level from the roots down to ``target``."""
    path = ontology.path(target)
    parents: list[str | None] = [None, *path[:-1]]
    return [LevelExample(approval, i + 1, node, level_options(ontology, node), gold)
            for i, (node, gold) in enumerate(zip(parents, path, strict=True))]


def level_examples(ontology: Ontology, approval: Approval) -> tuple[list[LevelExample], str]:
    """Examples for one approval, or ``([], reason)`` when it can't be placed in the tree."""
    if approval.kind == "match":
        if approval.leaf not in ontology:
            return [], f"leaf {approval.leaf!r} is not in {ontology.ref}"
        return _walk(ontology, approval, approval.leaf), ""
    parent = approval.parent
    if not parent or not ontology.has_node(parent) or ontology.node(parent).is_leaf:
        return [], f"group {parent!r} is not in {ontology.ref}"
    out = _walk(ontology, approval, parent)
    options = approval.options or level_options(ontology, parent, exclude=[approval.leaf])
    out.append(LevelExample(approval, len(out) + 1, parent, dict(options), NONE_LABEL))
    if approval.leaf in ontology:  # it has joined the tree since: its path is right now too
        seen = {(ex.node, ex.gold) for ex in out}
        out.extend(ex for ex in _walk(ontology, approval, approval.leaf)
                   if (ex.node, ex.gold) not in seen)
    return out, ""


def in_holdout(group: str, fraction: float, seed: int = 0) -> bool:
    """Stable split: the same group lands on the same side whatever else is in the log."""
    digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < fraction


def export_dataset(ontology: Ontology, approvals: Sequence[Approval], out_dir: str | Path, *,
                   holdout: float = 0.2, seed: int = 0, log_sha256: str = "",
                   shuffle_options: bool = True) -> dict[str, Any]:
    """Write ``train.jsonl``, ``heldout.jsonl`` and ``manifest.json``; return the manifest."""
    if not 0.0 <= holdout < 1.0:
        raise ValueError("holdout must be in [0, 1)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    train: list[dict[str, Any]] = []
    heldout: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    counts: dict[str, Counter] = {"train": Counter(), "heldout": Counter()}
    for a in approvals:
        examples, reason = level_examples(ontology, a)
        if reason:
            skipped.append({"approval": a.id, "phrase": a.phrase, "reason": reason})
            continue
        held = in_holdout(a.group, holdout, seed)
        if held and a.source != "human":
            continue  # never train on a paraphrase of a held-out phrase
        split = "heldout" if held else "train"
        for ex in examples:
            if held:
                heldout.append(ex.eval_row())
            else:
                train.append(ex.train_row(rng if shuffle_options else None))
            counts[split][f"level:{ex.level}"] += 1
            counts[split][f"kind:{ex.kind}"] += 1
            counts[split][f"source:{a.source}"] += 1
    for name, rows in (("train.jsonl", train), ("heldout.jsonl", heldout)):
        with (out / name).open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    sources = Counter(a.source for a in approvals)
    manifest = {
        "created_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "ontology": ontology.ref,
        "ontology_sha256": ontology.sha256,
        "log_sha256": log_sha256,
        "approvals": {"human": sources.get("human", 0),
                      "synthetic": sources.get("synthetic", 0)},
        "holdout": holdout,
        "seed": seed,
        "train": {"rows": len(train), **dict(sorted(counts["train"].items()))},
        "heldout": {"rows": len(heldout), **dict(sorted(counts["heldout"].items()))},
        "skipped": skipped,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    return manifest
