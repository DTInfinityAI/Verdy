"""Resolvers: map an extracted candidate parameter to an ontology entry, or to "none".

ODD authoring runs LLM → resolver → LLM:

1. An LLM extracts candidate parameters with value ranges from the description.
2. An embedding :class:`~verdy.odd.shortlist.Shortlister` keeps the top-k ontology entries
   per candidate.
3. A **resolver** picks the matching entry or ``none``, with a probability.
4. An LLM drafts definitions only for the misses, flagged ``new_ontology_entry``.
5. A human approves every parameter.

Built-in resolvers:

``exact``
    Normalised string match of the candidate's name or source phrase against entry names
    and synonyms. Deterministic, no model.
``laya``
    `Laya <https://github.com/NandhaKishorM/laya>`_, an open-weight non-autoregressive
    decision model, run locally (no API key): one typed ``choice`` question per candidate
    whose options are the shortlist plus ``none``. ``pip install "verdy[laya]"``.
``llm``
    Claude resolves all candidates in one structured-output call.

Plug in your own with ``module:attribute`` naming a :class:`Resolver` subclass or factory.
Every decision becomes part of the parameter's provenance, so the evidence report shows
why "murky water" became ``turbidity``.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verdy.odd.ontology import NONE_LABEL, Ontology, OntologyEntry
from verdy.odd.shortlist import Scored, normalize

DEFAULT_MIN_PROBABILITY = 0.5
PROVENANCE_SHORTLIST = 5  # shortlist entries recorded in provenance


@dataclass
class Candidate:
    """A parameter the extraction step found in the description."""

    name: str
    phrase: str = ""
    description: str = ""
    category: str = "environment"
    type: str = "continuous"
    unit: str = ""
    range: list[float] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def query(self) -> str:
        """Text used to retrieve the shortlist."""
        parts = [self.phrase, self.name.replace("_", " "), self.description]
        if self.unit:
            parts.append(f"unit {self.unit}")
        return ". ".join(p for p in parts if p)

    def to_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {"mentioned_as": self.phrase or self.name,
                                 "proposed_name": self.name, "meaning": self.description,
                                 "type": self.type}
        if self.unit:
            state["unit"] = self.unit
        if self.range:
            state["range"] = self.range
        if self.values:
            state["values"] = self.values
        return state


@dataclass
class Resolution:
    candidate: Candidate
    entry: OntologyEntry | None
    probability: float | None
    resolver: str
    shortlist: list[Scored] = field(default_factory=list)
    model: str | None = None
    reason: str = ""
    proposed: str | None = None  # resolver's top choice when it fell below the threshold

    @property
    def matched(self) -> bool:
        return self.entry is not None

    @property
    def decision(self) -> str:
        return self.entry.name if self.entry is not None else NONE_LABEL

    def to_provenance(self, ontology: Ontology | None = None,
                      embedder: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"resolver": self.resolver}
        if self.model:
            out["model"] = self.model
        out["decision"] = self.decision
        if self.probability is not None:
            out["probability"] = round(float(self.probability), 4)
        out["candidate"] = self.candidate.name
        if self.candidate.phrase:
            out["phrase"] = self.candidate.phrase
        if self.proposed:
            out["proposed"] = self.proposed
        if self.reason:
            out["reason"] = self.reason
        if ontology is not None:
            out["ontology"] = ontology.ref
        if embedder:
            out["embedder"] = embedder
        out["shortlist"] = [{"entry": s.entry.name, "score": s.score}
                            for s in self.shortlist[:PROVENANCE_SHORTLIST]]
        return out


class Resolver(ABC):
    """Picks the ontology entry a candidate refers to from its shortlist, or none."""

    name = "resolver"
    model: str | None = None

    def __init__(self, min_probability: float = DEFAULT_MIN_PROBABILITY):
        if not 0.0 <= min_probability <= 1.0:
            raise ValueError("min_probability must be in [0, 1]")
        self.min_probability = min_probability

    @abstractmethod
    def resolve(self, candidate: Candidate, options: list[Scored],
                context: str) -> Resolution:
        """Resolve one candidate against its shortlist."""

    def resolve_all(self, items: Sequence[tuple[Candidate, list[Scored]]],
                    context: str) -> list[Resolution]:
        """Resolve every candidate. Override to batch (one model call for all)."""
        return [self.resolve(c, opts, context) for c, opts in items]

    def _decide(self, candidate: Candidate, options: list[Scored], label: str | None,
                probability: float | None, reason: str = "") -> Resolution:
        """A match only if ``label`` is in the shortlist and clears ``min_probability``."""
        by_name = {s.entry.name: s.entry for s in options}
        entry = by_name.get(label) if label else None
        proposed = None
        if entry is not None and probability is not None and probability < self.min_probability:
            proposed, entry = label, None
            reason = (reason + "; " if reason else "") + (
                f"below min_probability {self.min_probability}")
        elif label and label != NONE_LABEL and entry is None:
            proposed = label
            reason = (reason + "; " if reason else "") + "choice is not in the shortlist"
        return Resolution(candidate=candidate, entry=entry, probability=probability,
                          resolver=self.name, shortlist=list(options), model=self.model,
                          reason=reason, proposed=proposed)


class ExactResolver(Resolver):
    """Normalised string match on entry names and synonyms."""

    name = "exact"

    def resolve(self, candidate: Candidate, options: list[Scored],
                context: str) -> Resolution:
        keys = {normalize(candidate.name), normalize(candidate.phrase)} - {""}
        for s in options:
            names = {normalize(s.entry.name), *(normalize(x) for x in s.entry.synonyms)}
            hit = keys & names
            if hit:
                return self._decide(candidate, options, s.entry.name, 1.0,
                                    f"exact match on {sorted(hit)[0]!r}")
        return self._decide(candidate, options, None, None, "no exact name or synonym match")


LAYA_INSTRUCTIONS = (
    "A robot test engineer described an operating condition. Which ontology entry measures "
    "the same physical quantity? Answer none if no entry does."
)
LAYA_NONE_TEXT = "none of these: a different quantity that needs a new entry"
_LAYA_OPTION_CHARS = 160


class LayaResolver(Resolver):
    """Laya, run locally: one typed ``choice`` question per candidate.

    ``router`` is any object with Laya's ``predict(state, questions, **kwargs)`` contract;
    by default ``laya.Router(**router_options)`` is created on first use. ``checkpoint``
    pins a Laya checkpoint (``"english"``, ``"multilingual"``, ...); by default Laya's
    router picks one per request.
    """

    name = "laya"

    def __init__(self, min_probability: float = DEFAULT_MIN_PROBABILITY, *,
                 checkpoint: str | None = None, router: Any = None,
                 **router_options: Any):
        super().__init__(min_probability)
        self.checkpoint = checkpoint
        self._router = router
        self._router_options = router_options
        if router is None:
            try:
                import laya  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    'the laya resolver needs Laya: pip install "verdy[laya]"') from exc

    @property
    def router(self) -> Any:
        if self._router is None:
            from laya import Router

            self._router = Router(**self._router_options)
        return self._router

    def resolve(self, candidate: Candidate, options: list[Scored],
                context: str) -> Resolution:
        criteria = {s.entry.name: s.entry.text()[:_LAYA_OPTION_CHARS] for s in options}
        criteria[NONE_LABEL] = LAYA_NONE_TEXT
        questions = {"entry": {"type": "choice", "instructions": LAYA_INSTRUCTIONS,
                               "criteria": criteria}}
        kwargs = {"model": self.checkpoint} if self.checkpoint else {}
        result = self.router.predict(candidate.to_state(), questions, **kwargs)
        answer = result["answers"]["entry"]
        label = answer["choice"]
        probs = answer.get("probabilities") or {}
        p = probs.get(label, answer.get("answer_confidence"))
        routed = (result.get("routing") or {}).get("model")
        self.model = f"laya:{routed}" if routed else "laya"
        if label == NONE_LABEL:
            best = max((k for k in probs if k != NONE_LABEL), key=probs.get, default=None)
            reason = f"best entry {best} at {probs[best]:.2f}" if best else ""
            return self._decide(candidate, options, None,
                                None if p is None else float(p), reason)
        return self._decide(candidate, options, label, None if p is None else float(p))


LLM_SYSTEM_PROMPT = """\
You map parameters extracted from a robot's operating-condition description to entries of \
a parameter ontology. For each candidate you get a short list of ontology entries. Pick the \
entry that measures the same physical quantity, even when wording or units differ \
("murky water" is turbidity). Answer "none" when no entry measures that quantity; a related \
but different quantity is "none". Give the probability (0-1) that your answer is right and a \
one-line reason."""

LLM_RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string"},
                    "entry": {"type": "string"},
                    "probability": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate", "entry", "probability", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


class LLMResolver(Resolver):
    """Claude resolves every candidate in one call. Needs ``pip install "verdy[llm]"``."""

    name = "llm"

    def __init__(self, min_probability: float = DEFAULT_MIN_PROBABILITY, *,
                 client: Any = None, model: str | None = None, effort: str = "medium"):
        from verdy.llm import DEFAULT_MODEL

        super().__init__(min_probability)
        self._client = client
        self.model = model or DEFAULT_MODEL
        self.effort = effort

    def resolve(self, candidate: Candidate, options: list[Scored],
                context: str) -> Resolution:
        return self.resolve_all([(candidate, options)], context)[0]

    def resolve_all(self, items: Sequence[tuple[Candidate, list[Scored]]],
                    context: str) -> list[Resolution]:
        from verdy.llm import LLMError, claude_client, create_json

        if not items:
            return []
        if self._client is None:
            self._client = claude_client()
        payload = [
            {"candidate": c.name, **c.to_state(),
             "options": [{"entry": s.entry.name, "definition": s.entry.text()} for s in opts]}
            for c, opts in items
        ]
        content = (f"Description:\n{context}\n\nCandidates and their options:\n"
                   + json.dumps(payload, indent=1))
        try:
            data, _ = create_json(
                self._client, system=LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}], schema=LLM_RESOLVE_SCHEMA,
                model=self.model, effort=self.effort,
            )
        except LLMError as exc:
            raise RuntimeError(f"llm resolver failed: {exc}") from exc
        by_candidate = {d["candidate"]: d for d in data["decisions"]}
        out = []
        for c, opts in items:
            d = by_candidate.get(c.name)
            if d is None:
                out.append(self._decide(c, opts, None, None, "model gave no decision"))
                continue
            p = max(0.0, min(1.0, float(d["probability"])))
            label = None if d["entry"] in ("", NONE_LABEL) else d["entry"]
            out.append(self._decide(c, opts, label, p, d.get("reason", "")))
        return out


RESOLVERS: dict[str, type[Resolver]] = {
    "exact": ExactResolver,
    "laya": LayaResolver,
    "llm": LLMResolver,
}


def make_resolver(spec: str | Resolver, **options: Any) -> Resolver:
    """A built-in resolver by name, ``module:attribute``, or an instance (returned as is)."""
    if isinstance(spec, Resolver):
        return spec
    if spec in RESOLVERS:
        return RESOLVERS[spec](**options)
    if ":" in spec:
        from verdy.config import import_object

        obj = import_object(spec, Path.cwd())
        resolver = obj(**options)
        if not isinstance(resolver, Resolver):
            raise ValueError(f"{spec} did not produce a verdy.odd.resolve.Resolver")
        return resolver
    raise ValueError(
        f"unknown resolver {spec!r}: use {', '.join(RESOLVERS)} or module:attribute")
