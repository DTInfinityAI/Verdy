"""Calls to Claude, with credentials from :mod:`verdy.secrets`.

Install with ``pip install "verdy[llm]"``. The API key is read from the
``ANTHROPIC_API_KEY`` secret (environment variable or the Verdy secrets file) and passed
straight to the SDK client; it is never logged or stored. If the secret is not set, the
SDK's own credential lookup is used (e.g. an ``ant auth login`` profile).
"""
from __future__ import annotations

import json
from typing import Any

from verdy.secrets import get_secret

DEFAULT_MODEL = "claude-opus-5-5"
API_KEY_SECRET = "ANTHROPIC_API_KEY"


class LLMError(RuntimeError):
    """Raised when Claude declines a request or the output is unusable."""


def claude_client(api_key_secret: str = API_KEY_SECRET) -> Any:
    """An ``anthropic.Anthropic`` client authenticated from Verdy's secrets."""
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError('Claude features need: pip install "verdy[llm]"') from exc
    secret = get_secret(api_key_secret, required=False)
    if secret is None:
        return anthropic.Anthropic()
    return anthropic.Anthropic(api_key=secret.reveal())


def create_json(
    client: Any,
    *,
    system: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_tokens: int = 16000,
) -> tuple[dict[str, Any], Any]:
    """Request a JSON object matching ``schema``; return it with the raw response.

    Uses structured outputs, and server-side fallback to another model if the request
    is declined.
    """
    response = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise LLMError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMError("the response was cut off at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text), response
