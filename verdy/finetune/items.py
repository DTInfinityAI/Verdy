"""Tokenize training rows into Laya's training-item format.

Laya's fine-tuning scripts (``notebooks/laya_finetune_typed_decisions_mps.py`` and the
Kaggle notebook in the Laya repository) train on a ``torch.save``-d list of items::

    {"ids": token ids, "markers": option-marker positions, "qtype": 0,
     "target": per-option probabilities, "label": index of the gold option}

built with ``laya.common.build_sequence``. :func:`build_items` produces exactly that from
``train.jsonl`` (written by ``verdy laya dataset``), the same way the scripts'
``build_training_item`` does, so the scripts train on your approvals unchanged: pass the file
with ``--items``. A cache without the scripts' ``.meta.json`` sidecar is used as-is.

Needs Laya and its dependencies (``pip install "verdy[laya]"``) and a local copy of the
base checkpoint (``model.safetensors``, ``rl_agent_config.json``, ``tokenizer/``).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

# The Laya fine-tuning scripts train with these budgets (they override the checkpoint's);
# items must be tokenized with the same ones.
TRAIN_MAX_LEN = 1024
TRAIN_HEAD_MAX_LEN = 256


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build_items(
    rows: Iterable[dict[str, Any]], *, tokenizer: Any, build_sequence: Callable[..., Any],
    render_options: Callable[[dict[str, Any]], list[str]], qtypes: dict[str, int],
    max_len: int = TRAIN_MAX_LEN, head_max_len: int = TRAIN_HEAD_MAX_LEN,
) -> tuple[list[dict[str, Any]], int]:
    """Training items from typed-decisions rows; returns ``(items, skipped)``.

    Mirrors ``build_training_item`` in Laya's fine-tuning script for ``choice`` questions.
    """
    items: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        state = json.loads(row["state"])
        questions = json.loads(row["questions"])
        gold = json.loads(row["gold"])
        for qid, q in questions.items():
            if qid not in gold or q["type"] != "choice":
                skipped += 1
                continue
            keys = list(q["criteria"])
            probs = gold[qid]["probabilities"]
            target = [float(probs.get(k, 0.0)) for k in keys]
            total = sum(target)
            target = [t / total for t in target] if total > 0 else [1.0 / len(keys)] * len(keys)
            spec = {"t": "choice", "ins": q["instructions"], "crit": q["criteria"]}
            sequence, markers = build_sequence(tokenizer, state, spec, max_len, head_max_len)
            if len(markers) != len(render_options({"t": "choice", "crit": q["criteria"]})):
                skipped += 1  # the head budget dropped options: unusable
                continue
            items.append({"ids": sequence, "markers": markers, "qtype": qtypes["choice"],
                          "target": target, "label": target.index(max(target))})
    return items, skipped


def build_items_file(train_jsonl: str | Path, model_dir: str | Path, output: str | Path, *,
                     max_len: int = TRAIN_MAX_LEN,
                     head_max_len: int = TRAIN_HEAD_MAX_LEN) -> tuple[int, int]:  # pragma: no cover
    """Tokenize ``train_jsonl`` with the checkpoint in ``model_dir`` and ``torch.save`` it."""
    try:
        import torch
        from laya.common import QTYPES, build_sequence, render_options
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise ImportError('building Laya training items needs: pip install "verdy[laya]"') \
            from exc
    model_dir = Path(model_dir)
    if not (model_dir / "tokenizer").is_dir():
        raise FileNotFoundError(
            f"{model_dir}/tokenizer not found; download the base checkpoint first (see "
            "docs/laya-finetuning.md)")
    try:  # the scripts patch the shipped tokenizer config before loading it
        from laya.agent import _fix_tokenizer_config

        _fix_tokenizer_config(str(model_dir))
    except ImportError:
        pass
    tokenizer = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    items, skipped = build_items(
        read_rows(train_jsonl), tokenizer=tokenizer, build_sequence=build_sequence,
        render_options=render_options, qtypes=QTYPES, max_len=max_len,
        head_max_len=head_max_len)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(items, output)
    meta = output.with_name(output.name + ".meta.json")
    if meta.exists():  # a stale sidecar would make the script rebuild from its own dataset
        meta.unlink()
    return len(items), skipped
