"""LLM-assisted ODD authoring with Claude, optionally resolved against an ontology.

Turns a plain-language description of operating conditions into a draft ODD. Every
drafted parameter is marked ``approved = false``, so a human must review it before strict
validation passes.

Without an ontology, Claude writes every parameter (``provenance.source = "llm"``).

With an ontology (:func:`draft_odd` ``ontology=...``) authoring runs LLM → resolver → LLM:
Claude extracts candidate parameters with ranges, an embedding shortlist and a
:mod:`resolver <verdy.odd.resolve>` (``exact``, ``laya``, ``llm`` or a plug-in) map each
candidate to an ontology entry or "none", and Claude drafts definitions only for the misses
(``new_ontology_entry: true``). Matched parameters take the ontology's name, unit, bounds,
distribution and grounding (``source = "ontology"``). Each parameter's provenance records the
resolver's decision, probability and shortlist.

Requires the optional dependency: ``pip install "verdy[llm]"``. The API key comes from
the ``ANTHROPIC_API_KEY`` secret (see :mod:`verdy.secrets`).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from verdy.llm import DEFAULT_MODEL, LLMError, claude_client, create_json
from verdy.odd.model import CATEGORIES, ODD, TYPES, Parameter
from verdy.odd.validation import ODDValidationError, check_odd
from verdy.spec import ODD_SPEC_VERSION

if TYPE_CHECKING:
    from verdy.odd.ontology import Ontology, OntologyEntry
    from verdy.odd.resolve import Candidate, Resolution, Resolver
    from verdy.odd.shortlist import Embedder

SYSTEM_PROMPT = """\
You help robotics engineers write an Operational Design Domain (ODD) for testing a robot \
policy. An ODD lists the parameters that vary across the conditions the robot must handle.

For each parameter give:
- name: snake_case identifier
- description: one sentence
- category: environment, platform, task, sensors or faults
- type: continuous or temporal (numeric, needs range [min, max] and a unit), categorical \
(needs values), or boolean
- distribution: the nominal frequency in deployment. Numeric: "uniform", \
"normal(mu, sigma)", "loguniform" (min > 0), "triangular(mode)" or "beta(a, b)". \
Categorical: "uniform". Boolean: "bernoulli(p)" with p = probability of true.
- confidence: 0-1, how sure you are the parameter and its domain are right

Use empty strings and empty lists for fields that do not apply to a parameter's type. \
Constraints are Python-syntax boolean expressions over parameter names, for combinations \
that cannot occur. Prefer physically realistic ranges, and include sensor degradations and \
faults the description implies. Do not invent requirements the description does not support; \
keep confidence low where you are guessing."""

_PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "type": {"type": "string", "enum": list(TYPES)},
        "unit": {"type": "string"},
        "range": {"type": "array", "items": {"type": "number"}},
        "values": {"type": "array", "items": {"type": "string"}},
        "distribution": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "name", "description", "category", "type", "unit", "range", "values",
        "distribution", "confidence",
    ],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "parameters": {"type": "array", "items": _PARAM_SCHEMA},
        "constraints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "description", "parameters", "constraints"],
    "additionalProperties": False,
}


class AuthoringError(RuntimeError):
    """Raised when the model cannot produce a valid ODD."""


def _to_odd_document(draft: dict[str, Any], version: str) -> dict[str, Any]:
    params = []
    for p in draft["parameters"]:
        out: dict[str, Any] = {
            "name": p["name"],
            "category": p["category"],
            "type": p["type"],
            "provenance": {
                "source": "llm",
                "confidence": max(0.0, min(1.0, float(p.get("confidence", 0.0)))),
                "approved": False,
            },
        }
        if p.get("description"):
            out["description"] = p["description"]
        if p.get("unit"):
            out["unit"] = p["unit"]
        if p["type"] in ("continuous", "temporal") and p.get("range"):
            out["range"] = p["range"]
        if p["type"] == "categorical" and p.get("values"):
            out["values"] = p["values"]
        if p.get("distribution"):
            out["distribution"] = p["distribution"]
        params.append(out)
    doc: dict[str, Any] = {
        "spec_version": ODD_SPEC_VERSION,
        "name": draft["name"],
        "version": version,
        "description": draft.get("description", ""),
        "parameters": params,
    }
    if draft.get("constraints"):
        doc["constraints"] = draft["constraints"]
    return doc


def draft_odd(
    description: str,
    *,
    client: Any = None,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    version: str = "0.1.0",
    max_attempts: int = 3,
    ontology: Ontology | str | None = None,
    resolver: Resolver | str = "exact",
    resolver_options: dict[str, Any] | None = None,
    embedder: Embedder | str | None = None,
    top_k: int | None = None,
) -> ODD:
    """Draft an ODD from a plain-language description of operating conditions.

    The draft is checked with Verdy's validator; validation errors are sent back to the
    model for correction, up to ``max_attempts`` requests in total.

    With ``ontology`` (an :class:`~verdy.odd.ontology.Ontology`, a bundled name such as
    ``"core"``, or a path), candidates are resolved against it first and Claude only writes
    the parameters the ontology lacks. ``resolver`` is ``"exact"``, ``"laya"``, ``"llm"``,
    ``module:attribute`` or a :class:`~verdy.odd.resolve.Resolver`.
    """
    if client is None:
        client = claude_client()
    if ontology is not None:
        return _draft_with_ontology(
            description, client=client, model=model, effort=effort, version=version,
            max_attempts=max_attempts, ontology=ontology, resolver=resolver,
            resolver_options=resolver_options, embedder=embedder, top_k=top_k,
        )

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Write an ODD for these operating conditions:\n\n{description}",
        }
    ]
    last_errors: list[str] = []
    for _ in range(max_attempts):
        try:
            draft, response = create_json(
                client, system=SYSTEM_PROMPT, messages=messages, schema=OUTPUT_SCHEMA,
                model=model, effort=effort,
            )
        except LLMError as exc:
            raise AuthoringError(f"cannot draft this ODD: {exc}") from exc
        doc = _to_odd_document(draft, version)
        report = check_odd(doc)
        if report.ok:
            return ODD.from_dict(doc)
        last_errors = report.errors
        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": "The ODD failed validation. Fix these errors and return the full ODD:\n- "
            + "\n- ".join(report.errors),
        })
    raise ODDValidationError(last_errors)


# --- ontology pipeline: LLM extracts -> shortlist -> resolver -> LLM authors misses ---------

EXTRACT_PROMPT = """\
You help robotics engineers write an Operational Design Domain (ODD) for testing a robot \
policy. Extract the parameters that vary across the operating conditions in the description: \
one per distinct physical quantity, condition or fault.

For each give:
- name: a snake_case name for the quantity
- phrase: the shortest words from the description that mention it, verbatim ("murky water")
- description: one sentence on what it measures
- category: environment, platform, task, sensors or faults
- type: continuous or temporal (numeric), categorical, or boolean
- unit: SI or customary unit of the range you give, "" if none
- range: [min, max] the description implies for numeric parameters, [] if it gives none
- values: categories the description implies for categorical parameters, [] otherwise
- confidence: 0-1, how sure you are this parameter belongs in the ODD

Also give the ODD a name and a one-sentence description, and constraints: Python-syntax \
boolean expressions over your parameter names for combinations that cannot occur. Do not \
invent conditions the description does not support."""

_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "phrase": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "type": {"type": "string", "enum": list(TYPES)},
        "unit": {"type": "string"},
        "range": {"type": "array", "items": {"type": "number"}},
        "values": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["name", "phrase", "description", "category", "type", "unit", "range",
                 "values", "confidence"],
    "additionalProperties": False,
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "candidates": {"type": "array", "items": _CANDIDATE_SCHEMA},
        "constraints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "description", "candidates", "constraints"],
    "additionalProperties": False,
}

NEW_ENTRY_PROMPT = SYSTEM_PROMPT + """

You are only writing the parameters that the team's parameter ontology does not have yet; \
each becomes a proposed new ontology entry, so make the definition reusable beyond this \
ODD. Write exactly one parameter per candidate, set candidate to the candidate's name, and \
keep that name unless the user says it is taken. Set parent to the id of the ontology group \
the new parameter belongs under (from the ontology reference below); when the user gives a \
placement, use it."""

NEW_ENTRIES_SCHEMA = {
    "type": "object",
    "properties": {
        "parameters": {
            "type": "array",
            "items": {
                **_PARAM_SCHEMA,
                "properties": {"candidate": {"type": "string"}, "parent": {"type": "string"},
                               **_PARAM_SCHEMA["properties"]},
                "required": ["candidate", "parent", *_PARAM_SCHEMA["required"]],
            },
        },
    },
    "required": ["parameters"],
    "additionalProperties": False,
}


def extract_candidates(
    description: str, *, client: Any, model: str = DEFAULT_MODEL, effort: str = "high",
    skill: str = "",
) -> tuple[dict[str, Any], list[Candidate]]:
    """Step 1: Claude turns the description into candidate parameters with ranges.

    ``skill`` is the rendered ontology skill (:func:`verdy.odd.skill.skill_prompt`), so
    candidate names follow the ontology's conventions. Returns the raw draft (name,
    description, constraints) and the candidates.
    """
    from verdy.odd.resolve import Candidate

    system = EXTRACT_PROMPT + (f"\n\nThe team's parameter ontology:\n\n{skill}" if skill else "")
    try:
        draft, _ = create_json(
            client, system=system,
            messages=[{"role": "user", "content": f"Operating conditions:\n\n{description}"}],
            schema=EXTRACT_SCHEMA, model=model, effort=effort,
        )
    except LLMError as exc:
        raise AuthoringError(f"cannot extract parameters: {exc}") from exc
    candidates, seen = [], set()
    for c in draft["candidates"]:
        name = _identifier(c["name"])
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append(Candidate(
            name=name, phrase=c.get("phrase", ""), description=c.get("description", ""),
            category=c["category"], type=c["type"], unit=c.get("unit", ""),
            range=[float(x) for x in c.get("range") or []],
            values=[str(v) for v in c.get("values") or []],
            confidence=max(0.0, min(1.0, float(c.get("confidence", 0.0)))),
        ))
    return draft, candidates


def _identifier(name: str) -> str:
    out = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip()).strip("_").lower()
    return f"p_{out}" if out[:1].isdigit() else out


def _units_match(a: str, b: str | None) -> bool:
    norm = lambda u: re.sub(r"[\s*^·]", "", (u or "").lower())  # noqa: E731
    return not a or norm(a) == norm(b)


def parameter_from_entry(entry: OntologyEntry, candidate: Candidate,
                         provenance: dict[str, Any]) -> dict[str, Any]:
    """An ODD parameter from an ontology entry, narrowed to the candidate's range/values."""
    from verdy.sampler.distributions import DistributionError, distribution_for

    notes = []
    out: dict[str, Any] = {"name": entry.name}
    if entry.description:
        out["description"] = entry.description
    out["category"] = entry.category
    out["type"] = entry.type
    if entry.unit:
        out["unit"] = entry.unit
    if entry.is_numeric and entry.range is not None:
        lo, hi = entry.range
        rng = candidate.range
        if len(rng) == 2 and rng[0] < rng[1] and _units_match(candidate.unit, entry.unit):
            clipped = [max(lo, rng[0]), min(hi, rng[1])]
            if clipped[0] < clipped[1]:
                out["range"] = clipped
                if clipped != rng:
                    notes.append(f"range {rng} clipped to ontology bounds [{lo}, {hi}]")
            else:
                out["range"] = [lo, hi]
                notes.append(f"range {rng} is outside ontology bounds; using [{lo}, {hi}]")
        else:
            out["range"] = [lo, hi]
            if len(rng) == 2:
                notes.append(f"description gave {rng} {candidate.unit}; convert to "
                             f"{entry.unit or 'the entry unit'} and narrow [{lo}, {hi}]")
            else:
                notes.append("range is the ontology's physical bounds; narrow it to this ODD")
    if entry.type == "categorical" and entry.values:
        wanted = {str(v).lower() for v in candidate.values}
        kept = [v for v in entry.values if str(v).lower() in wanted]
        out["values"] = kept or list(entry.values)
        extra = sorted(wanted - {str(v).lower() for v in entry.values})
        if extra:
            notes.append(f"values {extra} are not in the ontology")
    if entry.distribution:
        try:
            distribution_for(Parameter.from_dict({**out, "distribution": entry.distribution}))
            out["distribution"] = entry.distribution
        except DistributionError:
            notes.append(f"ontology distribution {entry.distribution} does not fit this range")
    if entry.grounding:
        out["grounding"] = dict(entry.grounding)
    prov = dict(provenance)
    if notes:
        prov["note"] = "; ".join(notes)
    out["provenance"] = prov
    return out


def _rename_identifiers(expression: str, mapping: dict[str, str]) -> str:
    return re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b",
                  lambda m: mapping.get(m.group(0), m.group(0)), expression)


def _draft_with_ontology(
    description: str, *, client: Any, model: str, effort: str, version: str,
    max_attempts: int, ontology: Ontology | str, resolver: Resolver | str,
    resolver_options: dict[str, Any] | None, embedder: Embedder | str | None,
    top_k: int | None,
) -> ODD:
    from verdy.odd.ontology import Ontology, load_ontology
    from verdy.odd.resolve import make_resolver
    from verdy.odd.shortlist import DEFAULT_TOP_K, Shortlister, embedder_name
    from verdy.odd.skill import render_skill, skill_prompt, skill_sha256

    onto = ontology if isinstance(ontology, Ontology) else load_ontology(ontology)
    res = make_resolver(resolver, **(resolver_options or {}))
    res.bind(onto)
    shortlister = Shortlister(onto, embedder, top_k or DEFAULT_TOP_K)
    embed_label = embedder_name(shortlister.embed)

    # 1. LLM extracts candidates, with the ontology's skill (conventions and branches).
    draft, candidates = extract_candidates(description, client=client, model=model,
                                           effort=effort, skill=skill_prompt(onto))
    if not candidates:
        raise AuthoringError("no parameters found in the description")

    # 2. Embeddings shortlist; 3. resolver decides.
    items = [(c, shortlister(c.query(), exact_keys=[c.name, c.phrase])) for c in candidates]
    resolutions = res.resolve_all(items, description)

    params: list[dict[str, Any]] = []
    rename: dict[str, str] = {}
    by_entry: dict[str, dict[str, Any]] = {}
    misses: list[Resolution] = []
    for r in resolutions:
        record = r.to_provenance(onto, embed_label)
        if r.entry is None:
            misses.append(r)
            continue
        rename[r.candidate.name] = r.entry.name
        if r.entry.name in by_entry:  # two mentions of one quantity: keep the first
            prov = by_entry[r.entry.name]["provenance"]
            prov.setdefault("also_mentioned_as", []).append(
                r.candidate.phrase or r.candidate.name)
            continue
        prob = r.probability if r.probability is not None else r.candidate.confidence
        param = parameter_from_entry(r.entry, r.candidate, {
            "source": "ontology", "confidence": round(float(prob), 4), "approved": False,
            "resolution": record,
        })
        by_entry[r.entry.name] = param
        params.append(param)

    # 4. LLM authors only the misses.
    new_params = _author_misses(
        description, misses, taken=set(by_entry) | {n.name for n in onto.nodes},
        client=client, model=model, effort=effort, max_attempts=max_attempts,
        ontology=onto, embedder=embed_label, matched=params,
    )
    for miss, p in new_params:
        rename[miss.candidate.name] = p["name"]
    params.extend(p for _, p in new_params)

    constraints = [_rename_identifiers(c, rename) for c in draft.get("constraints") or []]
    doc: dict[str, Any] = {
        "spec_version": ODD_SPEC_VERSION,
        "name": draft["name"],
        "version": version,
        "description": draft.get("description", ""),
        "parameters": params,
    }
    names = {p["name"] for p in params}
    from verdy.odd.constraints import ConstraintError, compile_constraints

    kept, dropped = [], []
    for c in constraints:
        try:
            ok = all(compiled.names <= names for compiled in compile_constraints([c]))
        except ConstraintError:
            ok = False
        (kept if ok else dropped).append(c)
    if kept:
        doc["constraints"] = kept
    authoring: dict[str, Any] = {
        "ontology": onto.ref, "resolver": res.name, "embedder": embed_label,
        "top_k": shortlister.k, "matched": len(params) - len(new_params),
        "new_entries": len(new_params), "skill_sha256": skill_sha256(render_skill(onto)),
    }
    if dropped:
        authoring["dropped_constraints"] = dropped
    doc["metadata"] = {"authoring": authoring}
    report = check_odd(doc)
    if not report.ok:
        raise ODDValidationError(report.errors)
    return ODD.from_dict(doc)


def _new_entry_parent(ontology: Ontology, proposed: str | None, category: str) -> str | None:
    """The group a new entry goes under: the proposal if it is a group in the parameter's
    category, else the category root (if the ontology has it)."""
    if proposed and ontology.is_group(proposed) and ontology.path(proposed)[0] == category:
        return proposed
    return category if ontology.has_node(category) else None


def _author_misses(
    description: str, misses: list[Resolution], *, taken: set[str], client: Any, model: str,
    effort: str, max_attempts: int, ontology: Ontology, embedder: str,
    matched: list[dict[str, Any]],
) -> list[tuple[Resolution, dict[str, Any]]]:
    """Step 4: Claude writes full definitions for candidates the ontology lacks."""
    from verdy.odd.skill import skill_prompt

    if not misses:
        return []
    payload = []
    branches = []
    for m in misses:
        item = {"candidate": m.candidate.name, **m.candidate.to_state(),
                "category": m.candidate.category}
        if m.placement and ontology.has_node(m.placement):
            item["placement"] = " → ".join(ontology.path(m.placement))
            branches.append(ontology.path(m.placement)[0])
        elif ontology.has_node(m.candidate.category):
            branches.append(m.candidate.category)
        payload.append(item)
    system = (NEW_ENTRY_PROMPT + "\n\nThe team's parameter ontology:\n\n"
              + skill_prompt(ontology, branches))
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (f"Operating conditions:\n\n{description}\n\nThe ontology has no entry for "
                    "these candidates. Write one parameter for each:\n"
                    + "\n".join(f"- {p}" for p in payload)
                    + "\n\nNames already taken: " + ", ".join(sorted(taken))),
    }]
    by_name = {m.candidate.name: m for m in misses}
    last_errors: list[str] = []
    for _ in range(max_attempts):
        try:
            data, response = create_json(
                client, system=system, messages=messages, schema=NEW_ENTRIES_SCHEMA,
                model=model, effort=effort,
            )
        except LLMError as exc:
            raise AuthoringError(f"cannot draft new parameters: {exc}") from exc
        out, errors, used = [], [], set(taken)
        for p in data["parameters"]:
            miss = by_name.get(p["candidate"])
            if miss is None or any(m is miss for m, _ in out):
                continue
            doc_param = _to_odd_document({"name": "x", "parameters": [p]}, "0")["parameters"][0]
            doc_param["name"] = _identifier(p["name"])
            if doc_param["name"] in used:
                errors.append(f"{p['candidate']}: name {doc_param['name']!r} is already taken")
            used.add(doc_param["name"])
            doc_param["provenance"]["new_ontology_entry"] = True
            parent = _new_entry_parent(ontology, miss.placement or p.get("parent"),
                                       doc_param["category"])
            if parent:
                doc_param["provenance"]["ontology_parent"] = parent
            doc_param["provenance"]["resolution"] = miss.to_provenance(ontology, embedder)
            out.append((miss, doc_param))
        missing = [m.candidate.name for m in misses if not any(r is m for r, _ in out)]
        if missing:
            errors.append("no parameter for candidates: " + ", ".join(missing))
        if not errors:
            report = check_odd({"name": "check", "version": "0",
                                "parameters": matched + [p for _, p in out]})
            errors = report.errors
        if not errors:
            return out
        last_errors = errors
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": "Fix these errors and return all the "
                         "parameters again:\n- " + "\n- ".join(errors)})
    raise ODDValidationError(last_errors)
