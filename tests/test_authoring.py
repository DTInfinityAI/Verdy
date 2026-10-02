import json
from types import SimpleNamespace

import pytest

from verdy.odd import ODDValidationError, check_odd
from verdy.odd.authoring import draft_odd


def param(**overrides):
    base = {"name": "lighting", "description": "Ambient light.", "category": "environment",
            "type": "continuous", "unit": "lux", "range": [20, 1000], "values": [],
            "distribution": "loguniform", "confidence": 0.8}
    return {**base, **overrides}


def response(draft, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=json.dumps(draft))],
    )


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


GOOD = {
    "name": "kitchen", "description": "A kitchen.", "constraints": [],
    "parameters": [
        param(),
        param(name="floor", type="categorical", unit="", range=[], values=["tile", "wood"],
              distribution="uniform"),
    ],
}


def test_draft_marks_parameters_unapproved():
    client = FakeClient(response(GOOD))
    odd = draft_odd("A kitchen robot.", client=client)
    assert [p.name for p in odd.parameters] == ["lighting", "floor"]
    assert odd["floor"].values == ["tile", "wood"] and odd["floor"].range is None
    assert all(p.provenance == {"source": "llm", "confidence": 0.8, "approved": False}
               for p in odd.parameters)
    assert not check_odd(odd.to_dict(), strict=True).ok
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_draft_retries_with_validation_errors():
    bad = {**GOOD, "parameters": [param(range=[100, 10])]}
    client = FakeClient(response(bad), response(GOOD))
    draft_odd("A kitchen robot.", client=client)
    assert len(client.calls) == 2
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "range min must be < max" in feedback


def test_draft_gives_up():
    bad = {**GOOD, "parameters": [param(range=[100, 10])]}
    with pytest.raises(ODDValidationError):
        draft_odd("x", client=FakeClient(response(bad), response(bad)), max_attempts=2)


def test_refusal():
    with pytest.raises(RuntimeError, match="declined"):
        draft_odd("x", client=FakeClient(response(GOOD, stop_reason="refusal")))
