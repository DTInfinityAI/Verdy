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
``laya-tree``
    Laya walks the ontology tree level by level (children plus ``none`` at each level),
    keeping the two best branches when they are close. The probability is the product along
    the path. "none" below a branch tells the miss-authoring step where a new entry belongs.
    Use it with a checkpoint fine-tuned on your approvals (:mod:`verdy.finetune`).
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
    path: list[dict[str, Any]] = field(default_factory=list)  # tree walk: [{node, p}]
    placement: str | None = None  # group where a new entry belongs (a "none" below it)

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
        if self.candidate.unit:
            out["unit"] = self.candidate.unit
        if self.proposed:
            out["proposed"] = self.proposed
        if self.reason:
            out["reason"] = self.reason
        if self.path:
            out["path"] = [dict(step) for step in self.path]
        if self.placement:
            out["placement"] = self.placement
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
        self.ontology: Ontology | None = None

    def bind(self, ontology: Ontology) -> None:
        """Called by authoring with the ontology being resolved against."""
        self.ontology = ontology

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


LAYA_CHECKPOINTS = ("english", "multilingual", "typed-decisions")


def laya_predictor(checkpoint: str | None = None, router: Any = None,
                   **router_options: Any) -> Any:
    """An object with Laya's ``predict(state, questions, ...)`` contract.

    ``router`` is used as is. Otherwise a checkpoint name (``english``, ``multilingual``,
    ``typed-decisions``) or none goes through ``laya.Router``, and anything else (a local
    fine-tuned directory or a Hub repo id) is loaded with ``laya.load``.
    """
    if router is not None:
        return router
    try:
        import laya
    except ImportError as exc:
        raise ImportError('the laya resolvers need Laya: pip install "verdy[laya]"') from exc
    if checkpoint is None or checkpoint in LAYA_CHECKPOINTS:
        return laya.Router(**router_options)
    return laya.load(checkpoint, **router_options)


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


class LayaTreeResolver(Resolver):
    """Laya walks the ontology tree: one ``choice`` among a node's children plus ``none`` per
    level, from the roots down to a leaf.

    Beam search, not greedy descent: at each level the runner-up branch is kept too when its
    probability is within ``beam_margin`` of the best, and at most ``beam_width`` paths are
    expanded (all of a level's questions go to Laya in one call). A path's probability is the
    product of its steps. The most probable finished path wins: a leaf is a match; "none"
    below a group is a miss whose ``placement`` is that group, so the new entry is drafted in
    the right place.

    ``checkpoint`` is a Laya checkpoint name, a local fine-tuned directory or a Hub repo id;
    see :func:`laya_predictor`. The question format comes from :mod:`verdy.odd.levels`, the
    same one the fine-tuning data uses.
    """

    name = "laya-tree"

    def __init__(self, min_probability: float = DEFAULT_MIN_PROBABILITY, *,
                 checkpoint: str | None = None, router: Any = None, beam_width: int = 2,
                 beam_margin: float = 0.2, **router_options: Any):
        super().__init__(min_probability)
        if beam_width < 1:
            raise ValueError("beam_width must be at least 1")
        if not 0.0 <= beam_margin <= 1.0:
            raise ValueError("beam_margin must be in [0, 1]")
        self.checkpoint = checkpoint
        self.beam_width = beam_width
        self.beam_margin = beam_margin
        self._predictor = router
        self._router_options = router_options
        if router is None:
            try:
                import laya  # noqa: F401
            except ImportError as exc:
                raise ImportError(
                    'the laya-tree resolver needs Laya: pip install "verdy[laya]"') from exc

    @property
    def predictor(self) -> Any:
        if self._predictor is None:
            self._predictor = laya_predictor(self.checkpoint, **self._router_options)
        return self._predictor

    def _ask(self, state: dict[str, Any], nodes: list[str | None]) -> list[dict[str, float]]:
        from verdy.odd.levels import QUESTION_ID, level_options, level_question

        assert self.ontology is not None
        questions = {f"{QUESTION_ID}{i}": level_question(level_options(self.ontology, n))
                     for i, n in enumerate(nodes)}
        kwargs = {"model": self.checkpoint} if self.checkpoint in LAYA_CHECKPOINTS else {}
        result = self.predictor.predict(state, questions, **kwargs)
        routed = (result.get("routing") or {}).get("model") or self.checkpoint
        self.model = f"laya:{routed}" if routed else "laya"
        out = []
        for qid in questions:
            answer = result["answers"][qid]
            probs = {k: float(v) for k, v in (answer.get("probabilities") or {}).items()}
            out.append(probs or {answer["choice"]: float(answer.get("answer_confidence", 1.0))})
        return out

    def resolve(self, candidate: Candidate, options: list[Scored],
                context: str) -> Resolution:
        from verdy.odd.levels import phrase_state

        if self.ontology is None:
            raise ValueError("the laya-tree resolver needs an ontology: call bind() first")
        onto = self.ontology
        state = phrase_state(candidate.phrase or candidate.name, candidate.name, candidate.unit)
        # A path is (node it stands at, probability, steps). Finished paths end at a leaf
        # (match) or at "none" below a group (miss placed in that group).
        frontier: list[tuple[str | None, float, list[dict[str, Any]]]] = [(None, 1.0, [])]
        done: list[tuple[float, OntologyEntry | None, str | None, list[dict[str, Any]]]] = []
        while frontier:
            dists = self._ask(state, [node for node, _, _ in frontier])
            expanded = []
            for (node, prob, steps), probs in zip(frontier, dists, strict=True):
                ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
                keep = ranked[:1] + [kv for kv in ranked[1:self.beam_width]
                                     if ranked[0][1] - kv[1] <= self.beam_margin]
                for label, p in keep:
                    path = steps + [{"node": label, "p": round(p, 4)}]
                    if label == NONE_LABEL or not onto.has_node(label):
                        done.append((prob * p, None, node, path))
                        continue
                    child = onto.node(label)
                    if isinstance(child, OntologyEntry):
                        done.append((prob * p, child, None, path))
                    else:
                        expanded.append((label, prob * p, path))
            best_done = max((d[0] for d in done), default=0.0)
            expanded.sort(key=lambda t: -t[1])
            # Products only shrink further down, so a path below the best finished one loses.
            frontier = [t for t in expanded[:self.beam_width] if t[1] > best_done]
        prob, leaf, placement, path = max(done, key=lambda d: d[0])
        proposed, reason = None, ""
        if leaf is not None and prob < self.min_probability:
            proposed, placement, leaf = leaf.name, leaf.parent, None
            reason = f"path probability below min_probability {self.min_probability}"
        elif leaf is None:
            reason = f"none below {placement}" if placement else "none of the root branches"
        return Resolution(candidate=candidate, entry=leaf, probability=prob,
                          resolver=self.name, shortlist=list(options), model=self.model,
                          reason=reason, proposed=proposed, path=path, placement=placement)


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
    "laya-tree": LayaTreeResolver,
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
