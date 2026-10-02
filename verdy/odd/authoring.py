"""LLM-assisted ODD authoring with Claude.

Turns a plain-language description of operating conditions into a draft ODD. Every
parameter the model proposes is marked ``provenance.source = "llm"`` and
``approved = false``, so a human must review it before strict validation passes.

Requires the optional dependency: ``pip install "verdy[llm]"``.
"""
from __future__ import annotations

import json
from typing import Any

from verdy.odd.model import CATEGORIES, ODD, TYPES
from verdy.odd.validation import ODDValidationError, check_odd
from verdy.spec import ODD_SPEC_VERSION

DEFAULT_MODEL = "claude-opus-5-5"

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
) -> ODD:
    """Draft an ODD from a plain-language description of operating conditions.

    The draft is checked with Verdy's validator; validation errors are sent back to the
    model for correction, up to ``max_attempts`` requests in total.
    """
    if client is None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError('LLM authoring needs: pip install "verdy[llm]"') from exc
        client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Write an ODD for these operating conditions:\n\n{description}",
        }
    ]
    last_errors: list[str] = []
    for _ in range(max_attempts):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise AuthoringError("the model declined to draft this ODD")
        if response.stop_reason == "max_tokens":
            raise AuthoringError("the draft was cut off; shorten the description")
        text = next(b.text for b in response.content if b.type == "text")
        doc = _to_odd_document(json.loads(text), version)
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
