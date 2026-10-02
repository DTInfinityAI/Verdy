import json

import pytest
import yaml

from verdy.odd import (
    Constraint,
    ConstraintError,
    ODDValidationError,
    check_odd,
    load_odd,
    save_odd,
    validate_odd,
)


def test_valid_odd(odd):
    assert odd.parameter_names == ["lighting", "floor_type", "person_speed", "person_delay", "wet"]
    assert odd["lighting"].range == (20.0, 1000.0)
    assert odd["wet"].domain == [False, True]
    assert "lighting" in odd and "nope" not in odd


def test_round_trip(odd):
    assert validate_odd(odd.to_dict()).to_dict() == odd.to_dict()


@pytest.mark.parametrize("suffix", [".yaml", ".json"])
def test_load_and_save(tmp_path, odd, suffix):
    path = tmp_path / f"odd{suffix}"
    save_odd(odd, path)
    text = path.read_text()
    assert (yaml.safe_load(text) if suffix == ".yaml" else json.loads(text))["name"] == "test-odd"
    assert load_odd(path).to_dict() == odd.to_dict()


def test_schema_errors_reported(odd_doc):
    odd_doc["parameters"][0]["category"] = "weather"
    del odd_doc["version"]
    report = check_odd(odd_doc)
    assert not report.ok
    text = "\n".join(report.errors)
    assert "version" in text and "weather" in text


def test_unknown_field_rejected(odd_doc):
    odd_doc["parameters"][0]["rnage"] = [0, 1]
    assert not check_odd(odd_doc).ok


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["parameters"].append(dict(d["parameters"][0])), "duplicate"),
        (lambda d: d["parameters"][0].update(range=[5, 1]), "range min must be < max"),
        (lambda d: d["parameters"][1].pop("values"), "needs 'values'"),
        (lambda d: d["parameters"][1].update(weights=[1, 2]), "weights"),
        (lambda d: d["parameters"][2].update(distribution="gamma(2)"), "unsupported"),
        (lambda d: d["parameters"][0].update(distribution="loguniform", range=[0, 5]), "min > 0"),
        (lambda d: d["parameters"][3].pop("range"), "needs 'range'"),
        (lambda d: d["parameters"][1].update(values=["a", "a", "b"], weights=None), "unique"),
        (lambda d: d["parameters"][0].update(default=5000), "outside the domain"),
        (lambda d: d.update(constraints=["lighting > speed"]), "unknown parameters: speed"),
        (lambda d: d.update(constraints=["__import__('os')"]), "unsupported"),
    ],
)
def test_semantic_errors(odd_doc, mutate, message):
    mutate(odd_doc)
    odd_doc["parameters"] = [{k: v for k, v in p.items() if v is not None}
                             for p in odd_doc["parameters"]]
    report = check_odd(odd_doc)
    assert not report.ok
    assert any(message in e for e in report.errors), report.errors
    with pytest.raises(ODDValidationError):
        validate_odd(odd_doc)


def test_unapproved_llm_parameter(odd_doc):
    odd_doc["parameters"][0]["provenance"] = {"source": "llm", "confidence": 0.7}
    report = check_odd(odd_doc)
    assert report.ok and any("not been approved" in w for w in report.warnings)
    assert not check_odd(odd_doc, strict=True).ok
    odd_doc["parameters"][0]["provenance"]["approved"] = True
    assert check_odd(odd_doc, strict=True).ok


def test_constraints():
    c = Constraint("speed <= 1.5 or lux >= 100 and floor in ['tile', 'wood']")
    assert c.names == {"speed", "lux", "floor"}
    assert c({"speed": 1.0, "lux": 10, "floor": "rug"})
    assert not c({"speed": 2.0, "lux": 10, "floor": "tile"})
    assert c({"speed": 2.0, "lux": 200, "floor": "tile"})
    assert Constraint("abs(a - b) < 1 and not flag")({"a": 1.0, "b": 1.5, "flag": False})
    assert Constraint("0 < x < 1")({"x": 0.5})


@pytest.mark.parametrize(
    "expr", ["x.__class__", "open('f')", "[i for i in x]", "lambda: 1", "x(1)", "a = 1"]
)
def test_constraints_reject_unsafe(expr):
    with pytest.raises(ConstraintError):
        Constraint(expr)


def test_example_odd_is_valid():
    from conftest import EXAMPLES

    odd = load_odd(EXAMPLES / "home_robot" / "odd.yaml", strict=True)
    assert len(odd.parameters) == 8
