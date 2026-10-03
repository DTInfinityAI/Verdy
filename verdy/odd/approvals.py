"""The approval log: every human-approved resolution, as phrase → leaf.

Each time a human approves a drafted ODD, ``verdy ontology log`` (or ``verdy ontology add``)
appends one record per approved parameter to a JSON Lines log
(default ``.verdy/ontology/approvals.jsonl``). The log is the training data for the Laya tree
walker (:mod:`verdy.finetune`), and an audit trail of how wording maps to parameters.

Records store **phrase → leaf**, not phrase → path. Per-level training examples are
regenerated from the *current* tree, so reorganising the ontology never invalidates the log.

- ``kind: match``: the phrase names an existing leaf. If the human corrected the resolver,
  the leaf is the corrected one, and ``resolver`` keeps what was proposed.
- ``kind: new``: the ontology had no leaf, so the right answer at ``parent`` was "none".
  ``options`` snapshots the choices shown there at the time ({id: gloss}), so the "none"
  example stays correct after the new leaf joins the tree.
- ``source: synthetic``: an LLM paraphrase of a human-approved phrase (``of`` is its id).
  Train on these, but evaluate only on ``source: human``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from verdy.odd.levels import level_options, phrase_state
from verdy.odd.model import ODD
from verdy.odd.ontology import Ontology

DEFAULT_LOG = Path(".verdy/ontology/approvals.jsonl")
KINDS = ("match", "new")
SOURCES = ("human", "synthetic")


@dataclass
class Approval:
    phrase: str
    leaf: str
    kind: str = "match"
    source: str = "human"
    state: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    options: dict[str, str] | None = None
    of: str | None = None
    ontology: str = ""
    odd: str = ""
    resolver: dict[str, Any] = field(default_factory=dict)
    logged_at: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        if not self.state:
            self.state = phrase_state(self.phrase)
        if not self.id:
            key = json.dumps([self.phrase.lower(), self.leaf, self.kind, self.source,
                              self.parent, self.of], separators=(",", ":"))
            self.id = hashlib.sha256(key.encode()).hexdigest()[:16]

    @property
    def group(self) -> str:
        """Records that must stay in the same train/held-out split."""
        return self.of or self.id

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Approval:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def approvals_from_odd(odd: ODD, ontology: Ontology) -> tuple[list[Approval], list[str]]:
    """Approval records for every approved, resolved parameter of ``odd``.

    Returns the records and notes on parameters that were skipped.
    """
    out: list[Approval] = []
    notes: list[str] = []
    now = _now()
    for p in odd.parameters:
        prov = p.provenance
        res = prov.get("resolution")
        if not res:
            continue
        new = bool(prov.get("new_ontology_entry"))
        if not p.approved:
            notes.append(f"{p.name}: not approved")
            continue
        if not new and p.name not in ontology:
            notes.append(f"{p.name}: not a leaf of {ontology.ref}; approve it as a new entry")
            continue
        parent = None
        options = None
        if new:
            parent = prov.get("ontology_parent") or res.get("placement") or p.category
            if not ontology.has_node(parent):
                notes.append(f"{p.name}: parent {parent!r} is not in {ontology.ref}")
                continue
            options = level_options(ontology, parent, exclude=[p.name])
        phrases = [res.get("phrase") or res.get("candidate") or p.name,
                   *prov.get("also_mentioned_as", [])]
        for phrase in dict.fromkeys(phrases):
            out.append(Approval(
                phrase=phrase, leaf=p.name, kind="new" if new else "match",
                state=phrase_state(phrase, res.get("candidate", ""), res.get("unit", "")),
                parent=parent, options=options, ontology=ontology.ref,
                odd=f"{odd.name}@{odd.version}",
                resolver={k: res[k] for k in ("resolver", "decision", "probability")
                          if k in res},
                logged_at=now,
            ))
    return out, notes


def read_approvals(path: str | Path = DEFAULT_LOG) -> list[Approval]:
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Approval.from_dict(json.loads(line)))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{lineno}: bad approval record: {exc}") from exc
    return out


def append_approvals(records: Iterable[Approval], path: str | Path = DEFAULT_LOG) -> int:
    """Append records not already in the log; return how many were added."""
    path = Path(path)
    seen = {a.id for a in read_approvals(path)}
    new = []
    for a in records:
        if a.id not in seen:
            seen.add(a.id)
            new.append(a)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for a in new:
                fh.write(json.dumps(a.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return len(new)


def log_sha256(path: str | Path) -> str:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


PARAPHRASE_PROMPT = """\
You write paraphrases of short phrases that robot test engineers use for operating \
conditions, to augment training data for a classifier. For each phrase, write varied ways an \
engineer, operator or customer might say the same thing: shorter, longer, jargon, casual \
("poor vis", "silty", "milky water" for murky water). Keep the same physical meaning, never \
broaden or narrow it, and don't repeat the original."""

PARAPHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"},
                               "paraphrases": {"type": "array", "items": {"type": "string"}}},
                "required": ["id", "paraphrases"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def paraphrase(records: Sequence[Approval], *, n: int = 3, client: Any = None,
               model: str | None = None, batch: int = 40) -> list[Approval]:
    """Synthetic approvals: ``n`` LLM paraphrases per human-approved record that has none yet.

    Each paraphrase copies its source record (leaf, kind, parent, options snapshot) with
    ``source: synthetic`` and ``of`` set to the source id.
    """
    from verdy.llm import DEFAULT_MODEL, LLMError, claude_client, create_json

    done = {a.of for a in records if a.source == "synthetic"}
    todo = [a for a in records if a.source == "human" and a.id not in done]
    if not todo:
        return []
    if client is None:
        client = claude_client()
    out: list[Approval] = []
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        listing = "\n".join(f"- id {a.id}: {a.phrase!r}" for a in chunk)
        try:
            data, _ = create_json(
                client, system=PARAPHRASE_PROMPT,
                messages=[{"role": "user", "content":
                           f"Write {n} paraphrases for each phrase:\n{listing}"}],
                schema=PARAPHRASE_SCHEMA, model=model or DEFAULT_MODEL, effort="low",
            )
        except LLMError as exc:
            raise RuntimeError(f"paraphrasing failed: {exc}") from exc
        by_id = {a.id: a for a in chunk}
        now = _now()
        for item in data["items"]:
            src = by_id.get(item["id"])
            if src is None:
                continue
            seen = {src.phrase.lower()}
            for text in item["paraphrases"][:n]:
                text = text.strip()
                if not text or text.lower() in seen:
                    continue
                seen.add(text.lower())
                out.append(Approval(
                    phrase=text, leaf=src.leaf, kind=src.kind, source="synthetic",
                    state=phrase_state(text, src.state.get("proposed_name", ""),
                                       src.state.get("unit", "")),
                    parent=src.parent, options=src.options, of=src.id,
                    ontology=src.ontology, odd=src.odd, logged_at=now,
                ))
    return out
