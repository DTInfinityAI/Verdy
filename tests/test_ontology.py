import json
from types import SimpleNamespace

import pytest
import yaml

from verdy.cli import main
from verdy.odd import ODDValidationError, check_odd, load_odd, save_odd
from verdy.odd.authoring import draft_odd, parameter_from_entry
from verdy.odd.model import Parameter
from verdy.odd.ontology import (
    Ontology,
    OntologyEntry,
    OntologyError,
    load_ontology,
    save_ontology,
)
from verdy.odd.resolve import (
    Candidate,
    ExactResolver,
    LayaResolver,
    LLMResolver,
    Resolver,
    make_resolver,
)
from verdy.odd.shortlist import HashingEmbedder, Shortlister


def response(data, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="text", text=json.dumps(data))])


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeLaya:
    """Laya's Router.predict contract: answers[q] = {choice, probabilities, ...}."""

    def __init__(self, picks):
        self.picks = picks  # candidate proposed_name -> (label, probability)
        self.calls = []

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        label, p = self.picks[state["proposed_name"]]
        labels = list(questions["entry"]["criteria"])
        rest = (1 - p) / (len(labels) - 1)
        probs = {k: (p if k == label else rest) for k in labels}
        return {"answers": {"entry": {"type": "choice", "choice": label,
                                      "probabilities": probs, "answer_confidence": p}},
                "routing": {"model": "english"}}


def cand(name, **kw):
    base = {"name": name, "phrase": "", "description": "", "category": "environment",
            "type": "continuous", "unit": "", "range": [], "values": [], "confidence": 0.7}
    return {**base, **kw}


EXTRACTION = {
    "name": "jacket-inspection", "description": "ROV inspects jacket legs.",
    "candidates": [
        cand("water_clarity", phrase="murky water", description="How murky the water is.",
             unit="NTU", range=[5, 50]),
        cand("current", phrase="strong current", description="Water current speed.",
             unit="knots", range=[1, 3]),
        cand("leg_spacing", phrase="close jacket legs", description="Gap between legs.",
             category="task", unit="m", range=[2, 6]),
    ],
    "constraints": ["water_clarity < 40 or current < 2.5", "leg_spacing > 1"],
}

NEW_ENTRIES = {"parameters": [{
    "candidate": "leg_spacing", "name": "jacket_leg_spacing",
    "description": "Clear gap between adjacent jacket legs.", "category": "task",
    "type": "continuous", "unit": "m", "range": [2, 6], "values": [],
    "distribution": "uniform", "confidence": 0.6,
}]}

LAYA_PICKS = {"water_clarity": ("turbidity", 0.93), "current": ("current_speed", 0.88),
              "leg_spacing": ("none", 0.81)}


# --- ontology --------------------------------------------------------------------------


def test_core_entries_make_valid_parameters():
    onto = load_ontology("core")
    assert onto.ref == "core@0.1.0" and "turbidity" in onto
    params = [parameter_from_entry(e, Candidate(e.name), {"source": "ontology",
                                                          "approved": False})
              for e in onto.entries]
    report = check_odd({"name": "all", "version": "0", "parameters": params})
    assert report.ok, report.errors


@pytest.mark.parametrize("entries, match", [
    ([{"name": "a", "category": "environment", "type": "continuous", "range": [0, 1]}] * 2,
     "duplicate"),
    ([{"name": "none", "category": "environment", "type": "boolean"}], "reserved"),
    ([{"name": "a", "category": "environment", "type": "continuous"}], "range"),
    ([{"name": "a", "category": "weather", "type": "boolean"}], "category"),
])
def test_invalid_ontology(entries, match):
    with pytest.raises(OntologyError, match=match):
        Ontology.from_dict({"name": "x", "version": "1", "entries": entries})


def test_ontology_roundtrip(tmp_path):
    onto = load_ontology("core")
    save_ontology(onto, tmp_path / "o.yaml")
    assert load_ontology(tmp_path / "o.yaml").to_dict() == onto.to_dict()


# --- shortlist and resolvers ------------------------------------------------------------


def test_shortlist_ranks_lexical_overlap_and_exact_hits_first():
    sl = Shortlister(load_ontology("core"), HashingEmbedder(), k=5)
    top = [s.entry.name for s in sl("strong current. water current speed")]
    assert "current_speed" in top[:2]
    hits = sl("whatever", exact_keys=["Murky water"])
    assert hits[0].entry.name == "turbidity" and hits[0].score == 1.0
    assert len(sl("x")) == 5


def test_exact_resolver():
    sl = Shortlister(load_ontology("core"), k=10)
    c = Candidate("water_clarity", phrase="murky water")
    r = ExactResolver().resolve(c, sl(c.query(), [c.name, c.phrase]), "")
    assert r.decision == "turbidity" and r.probability == 1.0
    c = Candidate("leg_spacing", phrase="close jacket legs")
    r = ExactResolver().resolve(c, sl(c.query()), "")
    assert r.decision == "none" and r.probability is None


def test_laya_resolver_question_and_threshold():
    sl = Shortlister(load_ontology("core"), k=8)
    router = FakeLaya({"water_clarity": ("turbidity", 0.93), "glare": ("glare", 0.3),
                       "leg_spacing": ("none", 0.8)})
    res = LayaResolver(router=router, checkpoint="english")
    c = Candidate("water_clarity", phrase="murky water", unit="NTU", range=[5, 50])
    r = res.resolve(c, sl(c.query(), [c.phrase]), "ctx")
    state, questions, kwargs = router.calls[0]
    q = questions["entry"]
    assert q["type"] == "choice" and "none" in q["criteria"] and len(q["criteria"]) == 9
    assert state["mentioned_as"] == "murky water" and kwargs == {"model": "english"}
    prov = r.to_provenance()
    assert prov["decision"] == "turbidity" and prov["probability"] == 0.93
    assert prov["model"] == "laya:english" and prov["shortlist"][0]["entry"] == "turbidity"

    c = Candidate("glare", phrase="sun glare")
    r = res.resolve(c, sl(c.query(), [c.phrase]), "ctx")
    assert r.decision == "none" and r.proposed == "glare" and "min_probability" in r.reason

    c = Candidate("leg_spacing")
    r = res.resolve(c, sl(c.query()), "ctx")
    assert r.decision == "none" and r.probability == 0.8 and r.reason.startswith("best entry")


def test_laya_resolver_needs_laya(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_laya(name, *args, **kwargs):
        if name == "laya":
            raise ImportError("no laya")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_laya)
    with pytest.raises(ImportError, match=r"verdy\[laya\]"):
        LayaResolver()


def test_llm_resolver_batches_one_call():
    sl = Shortlister(load_ontology("core"), k=5)
    items = [(Candidate(n, phrase=ph), sl(ph)) for n, ph in
             [("water_clarity", "murky water"), ("leg_spacing", "jacket legs")]]
    client = FakeClient(response({"decisions": [
        {"candidate": "water_clarity", "entry": "turbidity", "probability": 0.9,
         "reason": "murky = turbid"},
        {"candidate": "leg_spacing", "entry": "made_up", "probability": 0.7, "reason": ""},
    ]}))
    out = LLMResolver(client=client).resolve_all(items, "ctx")
    assert len(client.calls) == 1
    assert out[0].decision == "turbidity" and out[0].reason == "murky = turbid"
    assert out[1].decision == "none" and out[1].proposed == "made_up"


def test_make_resolver(tmp_path, monkeypatch):
    assert isinstance(make_resolver("exact"), ExactResolver)
    assert make_resolver("exact", min_probability=0.7).min_probability == 0.7
    (tmp_path / "myres.py").write_text(
        "from verdy.odd.resolve import Resolver\n\n"
        "class AlwaysNone(Resolver):\n"
        "    name = 'always-none'\n\n"
        "    def resolve(self, candidate, options, context):\n"
        "        return self._decide(candidate, options, None, 1.0)\n\n"
        "make = AlwaysNone\n")
    monkeypatch.chdir(tmp_path)
    assert make_resolver("myres:make").name == "always-none"
    with pytest.raises(ValueError, match="unknown resolver"):
        make_resolver("nope")


# --- authoring pipeline -----------------------------------------------------------------


def test_pipeline_llm_laya_llm():
    client = FakeClient(response(EXTRACTION), response(NEW_ENTRIES))
    router = FakeLaya(LAYA_PICKS)
    odd = draft_odd("Murky water near the jacket legs, strong current.", client=client,
                    ontology="core", resolver=LayaResolver(router=router))
    assert len(client.calls) == 2  # extraction + misses only
    assert [p.name for p in odd.parameters] == ["turbidity", "current_speed",
                                                 "jacket_leg_spacing"]

    turb = odd["turbidity"]
    assert turb.range == (5.0, 50.0) and turb.unit == "NTU"
    assert turb.provenance["source"] == "ontology" and not turb.approved
    res = turb.provenance["resolution"]
    assert res["decision"] == "turbidity" and res["probability"] == 0.93
    assert res["phrase"] == "murky water" and res["candidate"] == "water_clarity"
    assert res["resolver"] == "laya" and res["ontology"] == "core@0.1.0"

    cur = odd["current_speed"]  # knots vs m/s: ontology bounds and a note to convert
    assert cur.range == (0.0, 5.0) and "convert" in cur.provenance["note"]

    new = odd["jacket_leg_spacing"]
    assert new.provenance["source"] == "llm" and new.provenance["new_ontology_entry"]
    assert new.provenance["resolution"]["decision"] == "none"
    assert odd.constraints == ["turbidity < 40 or current_speed < 2.5",
                               "jacket_leg_spacing > 1"]
    meta = odd.metadata["authoring"]
    assert meta == {"ontology": "core@0.1.0", "resolver": "laya", "embedder": "hashing",
                    "top_k": 15, "matched": 2, "new_entries": 1}
    doc = odd.to_dict()
    assert check_odd(doc).ok and not check_odd(doc, strict=True).ok
    for p in doc["parameters"]:
        p["provenance"]["approved"] = True
    assert check_odd(doc, strict=True).ok


def test_pipeline_no_misses_skips_authoring_call():
    extraction = {**EXTRACTION, "candidates": EXTRACTION["candidates"][:1], "constraints": []}
    client = FakeClient(response(extraction))
    odd = draft_odd("Murky water.", client=client, ontology="core", resolver="exact")
    assert len(client.calls) == 1
    assert odd["turbidity"].provenance["resolution"]["probability"] == 1.0


def test_pipeline_merges_duplicate_mentions_and_drops_dangling_constraints():
    extraction = {**EXTRACTION, "candidates": [
        cand("water_clarity", phrase="murky water"), cand("silt", phrase="silt"),
    ], "constraints": ["unknown_thing > 1"]}
    odd = draft_odd("x", client=FakeClient(response(extraction)), ontology="core")
    assert [p.name for p in odd.parameters] == ["turbidity"]
    assert odd["turbidity"].provenance["also_mentioned_as"] == ["silt"]
    assert odd.metadata["authoring"]["dropped_constraints"] == ["unknown_thing > 1"]


def test_pipeline_retries_taken_new_name():
    taken = {"parameters": [{**NEW_ENTRIES["parameters"][0], "name": "turbidity"}]}
    client = FakeClient(response(EXTRACTION), response(taken), response(NEW_ENTRIES))
    odd = draft_odd("x", client=client, ontology="core",
                    resolver=LayaResolver(router=FakeLaya(LAYA_PICKS)))
    assert "jacket_leg_spacing" in odd
    assert "already taken" in client.calls[2]["messages"][-1]["content"]


def test_pipeline_gives_up_on_bad_new_entries():
    bad = {"parameters": [{**NEW_ENTRIES["parameters"][0], "range": [6, 2]}]}
    client = FakeClient(response(EXTRACTION), response(bad), response(bad))
    with pytest.raises(ODDValidationError):
        draft_odd("x", client=client, ontology="core", max_attempts=2,
                  resolver=LayaResolver(router=FakeLaya(LAYA_PICKS)))


def test_ontology_source_needs_approval():
    p = Parameter("x", "environment", "boolean", provenance={"source": "ontology"})
    assert not p.approved
    assert Parameter("x", "environment", "boolean",
                     provenance={"source": "ontology", "approved": True}).approved


# --- CLI ------------------------------------------------------------------------------


def test_cli_ontology_add(tmp_path, capsys):
    client = FakeClient(response(EXTRACTION), response(NEW_ENTRIES))
    odd = draft_odd("x", client=client, ontology="core",
                    resolver=LayaResolver(router=FakeLaya(LAYA_PICKS)))
    path = tmp_path / "odd.yaml"
    save_odd(odd, path)
    out = tmp_path / "onto.yaml"

    assert main(["ontology", "add", str(path), "--ontology", "core"]) == 2
    assert main(["ontology", "add", str(path), "--ontology", "core", "-o", str(out)]) == 0
    assert "skipped jacket_leg_spacing (not approved)" in capsys.readouterr().out
    assert not out.exists()

    doc = yaml.safe_load(path.read_text())
    for p in doc["parameters"]:
        p["provenance"]["approved"] = True
    path.write_text(yaml.safe_dump(doc))
    assert load_odd(path)["jacket_leg_spacing"].approved
    assert main(["ontology", "add", str(path), "--ontology", "core", "-o", str(out)]) == 0
    extended = load_ontology(out)
    entry = extended["jacket_leg_spacing"]
    assert isinstance(entry, OntologyEntry) and entry.synonyms == ["close jacket legs"]
    assert len(extended) == len(load_ontology("core")) + 1


def test_cli_author_prints_resolutions(tmp_path, capsys, monkeypatch):
    client = FakeClient(response(EXTRACTION), response(NEW_ENTRIES))
    monkeypatch.setattr("verdy.odd.authoring.claude_client", lambda: client)
    monkeypatch.setattr("verdy.odd.resolve.LayaResolver.__init__",
                        lambda self, min_probability=0.5, **kw: (
                            Resolver.__init__(self, min_probability),
                            setattr(self, "checkpoint", None),
                            setattr(self, "_router", FakeLaya(LAYA_PICKS)))[0])
    out = tmp_path / "odd.yaml"
    assert main(["author", "murky water, strong current, close legs", "-o", str(out),
                 "--resolver", "laya"]) == 0
    text = capsys.readouterr().out
    assert "'murky water'" in text and "-> turbidity  (p=0.93)" in text
    assert "jacket_leg_spacing  NEW ONTOLOGY ENTRY (p=0.81)" in text
    assert load_odd(out)["turbidity"].provenance["resolution"]["probability"] == 0.93
    assert main(["ontology", "list"]) == 0
