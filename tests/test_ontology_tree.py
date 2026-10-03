import json
from types import SimpleNamespace

import pytest
import yaml

from verdy.cli import main
from verdy.finetune.dataset import export_dataset, in_holdout, level_examples
from verdy.finetune.evaluate import compare_reports, ece, evaluate_rows
from verdy.finetune.items import build_items
from verdy.odd import ODD, save_odd
from verdy.odd.approvals import (
    Approval,
    append_approvals,
    approvals_from_odd,
    paraphrase,
    read_approvals,
)
from verdy.odd.levels import NONE_GLOSS, gloss, level_options, level_question, phrase_state
from verdy.odd.ontology import (
    Ontology,
    OntologyEntry,
    OntologyError,
    OntologyGroup,
    load_ontology,
    save_ontology,
)
from verdy.odd.resolve import Candidate, LayaTreeResolver
from verdy.odd.skill import render_skill, skill_prompt, write_skill


def leaf(id, parent, **kw):
    return {"id": id, "parent": parent, "type": "continuous", "range": [0, 1], **kw}


def tree(nodes, **kw):
    return {"name": "t", "version": "1", "nodes": nodes, **kw}


SMALL = tree([
    {"id": "environment", "definition": "The world around the robot."},
    {"id": "water", "parent": "environment", "label": "Water",
     "definition": "Conditions of water the robot works in or near."},
    {"id": "water_optical", "parent": "water", "label": "Optical",
     "definition": "How well light travels through the water."},
    leaf("turbidity", "water_optical", definition="Suspended particles reducing visibility.",
         aliases=["murky water"], unit="NTU"),
    leaf("current_speed", "water", unit="m/s", aliases=["current"]),
    leaf("lighting", "environment", unit="lux"),
    {"id": "task", "definition": "What the robot is asked to do."},
    leaf("goal_distance", "task", unit="m"),
])


# --- tree ---------------------------------------------------------------------------------


def test_tree_structure():
    onto = Ontology.from_dict(SMALL)
    assert onto.path("turbidity") == ["environment", "water", "water_optical", "turbidity"]
    assert onto["turbidity"].category == "environment"
    assert [n.name for n in onto.children("water")] == ["water_optical", "current_speed"]
    assert [n.name for n in onto.roots] == ["environment", "task"]
    assert "water" not in onto and onto.has_node("water")  # `in` is for leaves
    assert onto.check() == ([], [])


@pytest.mark.parametrize("nodes, match", [
    ([{"id": "weather"}], "roots must be ODD categories"),
    ([{"id": "environment"}, leaf("a", "nowhere")], "unknown parent"),
    ([{"id": "environment"}, leaf("a", "environment"), leaf("b", "a")], "is a leaf"),
    ([{"id": "environment", "parent": "x"}, {"id": "x", "parent": "environment"}],
     "roots must be|broken parent chain|unknown parent"),
    ([{"id": "environment"}, {"id": "environment"}], "duplicate"),
    ([{"id": "environment"}, {"id": "a", "parent": "environment", "type": "continuous"}],
     "range"),
])
def test_invalid_tree(nodes, match):
    with pytest.raises(OntologyError, match=match):
        Ontology.from_dict(tree(nodes))


def test_max_children_rule():
    nodes = [{"id": "task"}] + [leaf(f"p{i}", "task") for i in range(16)]
    onto = Ontology.from_dict(tree(nodes))
    errors, _ = onto.check()
    assert errors and "16 children, limit 15" in errors[0]
    assert onto.check(max_children=20)[0] == []
    assert Ontology.from_dict(tree(nodes, max_children=16)).check()[0] == []


def test_legacy_flat_format_still_loads():
    flat = {"name": "old", "version": "0.1.0", "entries": [
        {"name": "turbidity", "category": "environment", "type": "continuous",
         "range": [0, 1], "description": "Murk.", "synonyms": ["murky"]}]}
    onto = Ontology.from_dict(flat)
    assert onto.path("turbidity") == ["environment", "turbidity"]
    assert onto["turbidity"].synonyms == ["murky"] and onto["turbidity"].description == "Murk."


def test_tree_roundtrip_and_add(tmp_path):
    onto = load_ontology("core")
    save_ontology(onto, tmp_path / "o.yaml")
    again = load_ontology(tmp_path / "o.yaml")
    assert again.to_dict() == onto.to_dict() and again.sha256 == onto.sha256
    again.add(OntologyEntry("leg_spacing", "", "continuous", range=(0, 10), parent="task"))
    assert again.path("leg_spacing") == ["task", "leg_spacing"]
    with pytest.raises(OntologyError, match="not a group"):
        again.add(OntologyEntry("x", "", "boolean", parent="turbidity"))
    with pytest.raises(OntologyError, match="already exists"):
        again.add(OntologyGroup("water"))


# --- skill renderer -------------------------------------------------------------------------


def test_render_skill(tmp_path):
    onto = Ontology.from_dict(SMALL)
    files = render_skill(onto)
    assert set(files) == {"SKILL.md", "references/environment.md", "references/task.md"}
    skill = files["SKILL.md"]
    assert skill.startswith("---\nname: verdy-ontology-t\n")
    assert "Do not edit" in skill and "| `environment` |" in skill
    assert '"murky water" is `turbidity` (environment → water → water_optical → turbidity)' \
        in skill
    ref = files["references/environment.md"]
    assert "  - **water_optical** (Optical)" in ref and "`turbidity` (continuous; NTU; [0, 1])" \
        in ref

    assert write_skill(onto, tmp_path) == sorted(files, key=list(files).index)
    assert write_skill(onto, tmp_path, check=True) == []
    (tmp_path / "references" / "old_branch.md").write_text("stale")
    (tmp_path / "SKILL.md").write_text("edited by hand")
    assert write_skill(onto, tmp_path, check=True) == ["SKILL.md", "references/old_branch.md"]
    write_skill(onto, tmp_path)
    assert not (tmp_path / "references" / "old_branch.md").exists()

    prompt = skill_prompt(onto, ["task"])
    assert "# Task (`task`)" in prompt and "# Environment" not in prompt
    assert not prompt.startswith("---")


def test_bundled_skill_is_up_to_date():
    """skills/ontology-core is rendered from the bundled ontology; CI re-checks it too."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "skills" / "ontology-core"
    assert write_skill(load_ontology("core"), root, check=True) == []


# --- tree walker -----------------------------------------------------------------------------


class FakeTreeLaya:
    """Answers each level question from a table {node or 'ROOT': {child: p}}."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def predict(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        answers = {}
        for qid, q in questions.items():
            labels = list(q["criteria"])
            key = next((k for k, v in self.table.items() if set(v) <= set(labels)
                        and set(labels) - {"none"} == set(self._children(k))), None)
            probs = dict.fromkeys(labels, 0.0)
            probs.update(self.table[key])
            best = max(probs, key=probs.get)
            answers[qid] = {"choice": best, "probabilities": probs}
        return {"answers": answers}

    def _children(self, key):
        onto = Ontology.from_dict(SMALL)
        return [n.name for n in onto.children(None if key == "ROOT" else key)]


def walker(table, **kw):
    w = LayaTreeResolver(router=FakeTreeLaya(table), **kw)
    w.bind(Ontology.from_dict(SMALL))
    return w


def test_level_question_format():
    onto = Ontology.from_dict(SMALL)
    opts = level_options(onto, "water")
    assert opts == {"water_optical": "Optical: How well light travels through the water",
                    "current_speed": "Current speed"}  # no definition: the label alone
    q = level_question(opts)
    assert list(q["criteria"])[-1] == "none" and q["criteria"]["none"] == NONE_GLOSS
    long = OntologyGroup("g", label="G", description=" ".join(f"w{i}" for i in range(30)))
    assert gloss(long) == "G: " + " ".join(f"w{i}" for i in range(10))


def test_walker_descends_to_leaf():
    w = walker({"ROOT": {"environment": 0.9, "task": 0.05},
                "environment": {"water": 0.95},
                "water": {"water_optical": 0.9, "current_speed": 0.05},
                "water_optical": {"turbidity": 0.97}})
    r = w.resolve(Candidate("water_clarity", phrase="murky water", unit="NTU"), [], "")
    assert r.decision == "turbidity"
    assert r.probability == pytest.approx(0.9 * 0.95 * 0.9 * 0.97)
    assert [s["node"] for s in r.path] == ["environment", "water", "water_optical", "turbidity"]
    assert w._predictor.calls[0][0] == {"mentioned_as": "murky water",
                                        "proposed_name": "water_clarity", "unit": "NTU"}
    assert len(w._predictor.calls) == 4  # one call per level: the runner-up was not close


def test_walker_beam_recovers_from_close_early_mistake():
    # Greedy would go to task (0.5) and stop at a weak leaf; the beam also tries environment.
    w = walker({"ROOT": {"task": 0.5, "environment": 0.45},
                "task": {"goal_distance": 0.3, "none": 0.7},
                "environment": {"water": 0.99},
                "water": {"water_optical": 0.99},
                "water_optical": {"turbidity": 0.99}}, min_probability=0.4)
    r = w.resolve(Candidate("water_clarity", phrase="murky water"), [], "")
    assert r.decision == "turbidity" and r.probability == pytest.approx(0.45 * 0.99 ** 3)
    greedy = walker({"ROOT": {"task": 0.5, "environment": 0.45},
                     "task": {"goal_distance": 0.3, "none": 0.7},
                     "environment": {"water": 0.99}, "water": {"water_optical": 0.99},
                     "water_optical": {"turbidity": 0.99}}, beam_width=1, min_probability=0.4)
    r = greedy.resolve(Candidate("water_clarity", phrase="murky water"), [], "")
    assert r.decision == "none" and r.placement == "task"


def test_walker_none_gives_placement_and_threshold():
    w = walker({"ROOT": {"environment": 0.9}, "environment": {"water": 0.9},
                "water": {"none": 0.8, "water_optical": 0.1}})
    r = w.resolve(Candidate("wave_period", phrase="long swell period"), [], "")
    assert r.decision == "none" and r.placement == "water"
    assert r.reason == "none below water"
    assert r.to_provenance()["placement"] == "water"

    w = walker({"ROOT": {"task": 0.9}, "task": {"goal_distance": 0.5}}, min_probability=0.6)
    r = w.resolve(Candidate("route"), [], "")
    assert r.decision == "none" and r.proposed == "goal_distance" and r.placement == "task"


def test_walker_needs_bind():
    with pytest.raises(ValueError, match="bind"):
        LayaTreeResolver(router=FakeTreeLaya({})).resolve(Candidate("x"), [], "")


# --- approvals ---------------------------------------------------------------------------


def drafted_odd(approved=True):
    def prov(**kw):
        return {"source": "ontology", "confidence": 0.9, "approved": approved, **kw}
    return ODD.from_dict({"name": "rov", "version": "0.1.0", "parameters": [
        {"name": "turbidity", "category": "environment", "type": "continuous",
         "range": [5, 50], "unit": "NTU", "provenance": prov(resolution={
             "resolver": "laya-tree", "decision": "turbidity", "probability": 0.9,
             "candidate": "water_clarity", "phrase": "murky water", "unit": "NTU"},
             also_mentioned_as=["silt"])},
        {"name": "goal_distance", "category": "task", "type": "continuous", "range": [1, 2],
         "provenance": prov(resolution={"resolver": "laya-tree", "decision": "current_speed",
                                        "candidate": "route", "phrase": "route length"})},
        {"name": "wave_period", "category": "environment", "type": "continuous",
         "range": [2, 20], "provenance": {
             "source": "llm", "confidence": 0.6, "approved": approved,
             "new_ontology_entry": True, "ontology_parent": "water",
             "resolution": {"resolver": "laya-tree", "decision": "none",
                            "candidate": "wave_period", "phrase": "long swell",
                            "placement": "water"}}},
        {"name": "hand_written", "category": "task", "type": "boolean",
         "provenance": {"source": "human"}},
    ]})


def test_approvals_from_odd():
    onto = Ontology.from_dict(SMALL)
    recs, notes = approvals_from_odd(drafted_odd(), onto)
    by_phrase = {r.phrase: r for r in recs}
    assert set(by_phrase) == {"murky water", "silt", "route length", "long swell"}
    murky = by_phrase["murky water"]
    assert murky.kind == "match" and murky.leaf == "turbidity" and murky.source == "human"
    assert murky.state == {"mentioned_as": "murky water", "proposed_name": "water_clarity",
                           "unit": "NTU"}
    corrected = by_phrase["route length"]  # human corrected the resolver
    assert corrected.leaf == "goal_distance" and corrected.resolver["decision"] == "current_speed"
    new = by_phrase["long swell"]
    assert new.kind == "new" and new.parent == "water"
    assert set(new.options) == {"water_optical", "current_speed"}  # snapshot, without the leaf
    assert notes == []
    recs, notes = approvals_from_odd(drafted_odd(approved=False), onto)
    assert recs == [] and len(notes) == 3


def test_approval_log_dedupes(tmp_path):
    log = tmp_path / "a.jsonl"
    recs, _ = approvals_from_odd(drafted_odd(), Ontology.from_dict(SMALL))
    assert append_approvals(recs, log) == 4
    assert append_approvals(recs, log) == 0
    back = read_approvals(log)
    assert [r.id for r in back] == [r.id for r in recs] and back[0].phrase == "murky water"


def test_paraphrase_tags_synthetic():
    real = Approval("murky water", "turbidity", state=phrase_state("murky water", "wc", "NTU"))
    client = SimpleNamespace(calls=[])

    def create(**kw):
        client.calls.append(kw)
        data = {"items": [{"id": real.id, "paraphrases": ["poor vis", "silty", "Murky water"]}]}
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(data))])

    client.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
    syn = paraphrase([real], n=3, client=client)
    assert [s.phrase for s in syn] == ["poor vis", "silty"]  # the original is dropped
    assert all(s.source == "synthetic" and s.of == real.id and s.leaf == "turbidity"
               for s in syn)
    assert syn[0].state == {"mentioned_as": "poor vis", "proposed_name": "wc", "unit": "NTU"}
    assert paraphrase([real, *syn], client=client) == []  # already paraphrased


# --- fine-tuning data --------------------------------------------------------------------


def test_level_examples_match_and_none():
    onto = Ontology.from_dict(SMALL)
    ex, reason = level_examples(onto, Approval("murky water", "turbidity"))
    assert reason == "" and [(e.level, e.node, e.gold) for e in ex] == [
        (1, None, "environment"), (2, "environment", "water"),
        (3, "water", "water_optical"), (4, "water_optical", "turbidity")]
    assert set(ex[1].options) == {"water", "lighting"}  # siblings are the hard negatives

    new = Approval("long swell", "wave_period", kind="new", parent="water",
                   options={"water_optical": "Optical: old gloss"})
    ex, _ = level_examples(onto, new)
    assert [(e.node, e.gold) for e in ex] == [(None, "environment"), ("environment", "water"),
                                              ("water", "none")]
    assert ex[-1].options == {"water_optical": "Optical: old gloss"}  # the snapshot

    onto.add(OntologyEntry("wave_period", "", "continuous", range=(0, 30), parent="water"))
    ex, _ = level_examples(onto, new)  # it joined the tree: its path is right now too
    assert [(e.node, e.gold) for e in ex][-2:] == [("water", "none"), ("water", "wave_period")]

    assert level_examples(onto, Approval("x", "gone"))[1].startswith("leaf 'gone'")


def test_restructuring_regenerates_examples():
    data = json.loads(json.dumps(SMALL))
    for n in data["nodes"]:  # move turbidity up a level: water_optical is dissolved
        if n["id"] == "turbidity":
            n["parent"] = "water"
    data["nodes"] = [n for n in data["nodes"] if n["id"] != "water_optical"]
    ex, _ = level_examples(Ontology.from_dict(data), Approval("murky water", "turbidity"))
    assert [e.gold for e in ex] == ["environment", "water", "turbidity"]


def test_export_dataset(tmp_path):
    onto = Ontology.from_dict(SMALL)
    reals = [Approval(f"phrase {i}", "turbidity") for i in range(40)]
    reals.append(Approval("long swell", "wave_period", kind="new", parent="water"))
    syn = [Approval(f"para {i}", "turbidity", source="synthetic", of=r.id)
           for i, r in enumerate(reals[:20])]
    m = export_dataset(onto, reals + syn + [Approval("x", "gone")], tmp_path,
                       holdout=0.25, seed=3)
    train = [json.loads(x) for x in (tmp_path / "train.jsonl").read_text().splitlines()]
    held = [json.loads(x) for x in (tmp_path / "heldout.jsonl").read_text().splitlines()]
    assert m["train"]["rows"] == len(train) and m["heldout"]["rows"] == len(held) > 0
    assert m["skipped"][0]["reason"].startswith("leaf 'gone'")
    assert m["approvals"] == {"human": 42, "synthetic": 20}
    held_ids = {r["approval"] for r in held}
    held_groups = {a.id for a in reals if in_holdout(a.group, 0.25, 3)}
    assert held_ids == held_groups & {a.id for a in reals if a.leaf != "gone"}
    # no paraphrase of a held-out phrase is trained on, and held-out rows are human only
    trained = {r["approval"] for r in train}
    assert not {s.id for s in syn if s.of in held_groups} & trained
    assert all(r["source"] in ("human", "synthetic") for r in train)

    row = train[0]
    q = json.loads(row["questions"])["entry"]
    assert q["type"] == "choice" and "none" in q["criteria"]
    gold = json.loads(row["gold"])["entry"]["probabilities"]
    assert list(gold.values()) == [1.0] and next(iter(gold)) in q["criteria"]
    assert held[0]["expected"]["entry"] in held[0]["questions"]["entry"]["criteria"]
    assert held[0]["tags"][0].startswith("level:")
    assert list(held[0]["questions"]["entry"]["criteria"])[-1] == "none"  # canonical order

    # the split is stable as the log grows
    export_dataset(onto, reals + syn + [Approval(f"more {i}", "lighting") for i in range(30)],
                   tmp_path / "grown", holdout=0.25, seed=3)
    grown = (tmp_path / "grown" / "heldout.jsonl").read_text().splitlines()
    assert held_ids <= {json.loads(x)["approval"] for x in grown}


def test_build_items_mirrors_laya_script():
    onto = Ontology.from_dict(SMALL)
    ex, _ = level_examples(onto, Approval("murky water", "turbidity"))
    rows = [e.train_row() for e in ex]
    seen = []

    def build_sequence(tok, state, spec, max_len, head_max_len):
        seen.append((state, spec, max_len, head_max_len))
        return [1, 2, 3], list(range(len(spec["crit"])))

    items, skipped = build_items(rows, tokenizer=object(), build_sequence=build_sequence,
                                 render_options=lambda q: list(q["crit"]),
                                 qtypes={"choice": 0})
    assert skipped == 0 and len(items) == 4
    first = items[0]
    labels = list(json.loads(rows[0]["questions"])["entry"]["criteria"])
    assert first["qtype"] == 0 and first["label"] == labels.index("environment")
    assert sum(first["target"]) == 1.0 and seen[0][2:] == (1024, 256)
    assert seen[0][0] == {"mentioned_as": "murky water"}

    items, skipped = build_items(rows, tokenizer=None,
                                 build_sequence=lambda *a: ([1], [0]),  # options dropped
                                 render_options=lambda q: list(q["crit"]),
                                 qtypes={"choice": 0})
    assert items == [] and skipped == 4


# --- evaluation and promotion ----------------------------------------------------------


def eval_rows():
    onto = Ontology.from_dict(SMALL)
    rows = []
    for phrase, leaf_id in [("murky", "turbidity"), ("flow", "current_speed"),
                            ("lux", "lighting"), ("route", "goal_distance")]:
        ex, _ = level_examples(onto, Approval(phrase, leaf_id))
        rows += [e.eval_row() for e in ex]
    return rows


def test_evaluate_and_compare():
    rows = eval_rows()
    gold_by_q = {json.dumps(r["questions"], sort_keys=True) + json.dumps(r["state"]):
                 r["expected"]["entry"] for r in rows}

    def model(correct, conf):
        def predict(state, questions):
            gold = gold_by_q[json.dumps(questions, sort_keys=True) + json.dumps(state)]
            labels = list(questions["entry"]["criteria"])
            level_ok = correct(gold)
            choice = gold if level_ok else next(x for x in labels if x != gold)
            return {"answers": {"entry": {"choice": choice,
                                          "probabilities": {choice: conf}}}}
        return predict

    perfect = evaluate_rows(model(lambda g: True, 0.95), rows, checkpoint="new")
    assert perfect["overall"]["accuracy"] == 1.0 and perfect["overall"]["n"] == len(rows)
    assert set(perfect["by_level"]) == {"1", "2", "3", "4"}
    weak = evaluate_rows(model(lambda g: g not in ("water", "turbidity"), 0.95), rows)
    assert weak["by_level"]["4"]["accuracy"] < 1.0

    promote, reasons = compare_reports(perfect, weak)
    assert promote and reasons[0].startswith("accuracy")
    promote, reasons = compare_reports(weak, perfect)
    assert not promote and any("regressed" in r for r in reasons)

    overconfident = evaluate_rows(model(lambda g: g != "turbidity", 1.0), rows)
    humble = evaluate_rows(model(lambda g: g != "lighting", 0.91), rows)
    promote, reasons = compare_reports(overconfident, humble, tolerance=1.0)
    assert overconfident["overall"]["ece"] > humble["overall"]["ece"]
    assert not promote and any("calibration worse" in r for r in reasons)

    other = dict(perfect, dataset_sha256="x")
    assert compare_reports(other, weak) == (
        False, ["the reports were made on different held-out data; re-run both"])
    assert ece([0.9, 0.9], [True, False]) == pytest.approx(0.4)


# --- CLI ---------------------------------------------------------------------------------


def test_cli_validate_render_log_add_dataset_due(tmp_path, capsys):
    onto_path = tmp_path / "onto.yaml"
    onto_path.write_text(yaml.safe_dump(SMALL))
    assert main(["ontology", "validate", str(onto_path)]) == 0
    crowded = tree([{"id": "task"}] + [leaf(f"p{i}", "task") for i in range(16)])
    (tmp_path / "crowded.yaml").write_text(yaml.safe_dump(crowded))
    assert main(["ontology", "validate", str(tmp_path / "crowded.yaml")]) == 1
    assert "16 children, limit 15" in capsys.readouterr().out

    skill = tmp_path / "skill"
    assert main(["ontology", "render", str(onto_path), "-o", str(skill), "--check"]) == 1
    assert main(["ontology", "render", str(onto_path), "-o", str(skill)]) == 0
    assert main(["ontology", "render", str(onto_path), "-o", str(skill), "--check"]) == 0

    odd_path = tmp_path / "odd.yaml"
    save_odd(drafted_odd(), odd_path)
    log = tmp_path / "approvals.jsonl"
    assert main(["ontology", "log", str(odd_path), "--ontology", str(onto_path),
                 "--log", str(log)]) == 0
    assert "Logged 4 new approvals" in capsys.readouterr().out
    assert main(["ontology", "add", str(odd_path), "--ontology", str(onto_path),
                 "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "Logged 0 new approvals" in out
    assert "wave_period (under environment → water)" in out
    grown = load_ontology(onto_path)
    assert grown.path("wave_period") == ["environment", "water", "wave_period"]
    assert grown["wave_period"].synonyms == ["long swell"]

    data = tmp_path / "data"
    assert main(["laya", "dataset", "--ontology", str(onto_path), "--log", str(log),
                 "-o", str(data), "--holdout", "0.5"]) == 0
    manifest = json.loads((data / "manifest.json").read_text())
    assert manifest["approvals"]["human"] == 4
    assert main(["laya", "due", "--log", str(log), "--manifest", str(data / "manifest.json"),
                 "--every", "1"]) == 1
    assert main(["laya", "due", "--log", str(log), "--every", "4"]) == 0


def test_cli_eval_gate(tmp_path, monkeypatch, capsys):
    rows = eval_rows()
    held = tmp_path / "heldout.jsonl"
    held.write_text("".join(json.dumps(r) + "\n" for r in rows))
    gold = {json.dumps(r["state"]) + json.dumps(r["questions"], sort_keys=True):
            r["expected"]["entry"] for r in rows}

    class Runner:
        def __init__(self, good):
            self.good = good

        def predict(self, state, questions, **kw):
            g = gold[json.dumps(state) + json.dumps(questions, sort_keys=True)]
            choice = g if self.good else "none"
            return {"answers": {"entry": {"choice": choice, "probabilities": {choice: 0.9}}}}

    monkeypatch.setattr("verdy.odd.resolve.laya_predictor",
                        lambda spec, **kw: Runner(spec == "./laya_verdy"))
    report = tmp_path / "base.json"
    assert main(["laya", "eval", str(held), "--checkpoint", "english",
                 "-o", str(report)]) == 0
    assert main(["laya", "eval", str(held), "--checkpoint", "./laya_verdy",
                 "--baseline", str(report)]) == 0
    assert "PROMOTE ./laya_verdy" in capsys.readouterr().out
    assert main(["laya", "eval", str(held), "--checkpoint", "english",
                 "--baseline", "./laya_verdy"]) == 1
    assert "KEEP CURRENT" in capsys.readouterr().out
