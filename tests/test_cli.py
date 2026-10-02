import json
import shutil

import pytest
from conftest import EXAMPLES

from verdy.cli import main


def test_validate(capsys, tmp_path):
    assert main(["validate", str(EXAMPLES / "home_robot" / "odd.yaml"),
                 str(EXAMPLES / "home_robot" / "specs.yaml")]) == 0
    out = capsys.readouterr().out
    assert "OK (ODD with 8 parameters)" in out and "OK (3 STL specs)" in out
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nversion: '1'\nparameters: []\n")
    assert main(["validate", str(bad)]) == 2


def test_plan(capsys):
    assert main(["plan", "--max-failure-prob", "0.05"]) == 0
    assert "59 failure-free runs" in capsys.readouterr().out


def test_sample(capsys):
    assert main(["sample", str(EXAMPLES / "home_robot" / "odd.yaml"), "-n", "3", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_schema(capsys):
    assert main(["schema", "odd"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Verdy ODD"


@pytest.fixture
def example(tmp_path):
    dest = tmp_path / "examples"
    shutil.copytree(EXAMPLES, dest, ignore=shutil.ignore_patterns("reports"))
    return dest


@pytest.mark.parametrize(
    "config, runs, expected",
    [("home_robot/run.yaml", "300", 1), ("home_robot/run_tuned.yaml", "300", 3)],
)
def test_run_example(example, capsys, monkeypatch, config, runs, expected):
    monkeypatch.setenv("VERDY_SIGNING_KEY", "k")
    report = example / "out.report.json"
    code = main(["run", str(example / config), "--runs", runs, "-o", str(report),
                 "--sign", "hmac", "-q"])
    assert code == expected
    assert main(["verify", str(report), "--key-env", "VERDY_SIGNING_KEY"]) == 0
    assert "signature OK (hmac-sha256)" in capsys.readouterr().out
    data = json.loads(report.read_text())
    data["runs"][0]["failed"] = not data["runs"][0]["failed"]
    report.write_text(json.dumps(data))
    assert main(["verify", str(report)]) == 1


def test_run_full_examples(example, capsys):
    assert main(["run", str(example / "home_robot/run_tuned.yaml"), "-q"]) == 0  # PASS
    assert main(["run", str(example / "log_replay/run.yaml"), "-q"]) == 3  # INCONCLUSIVE
    out = capsys.readouterr().out
    assert "Runs: 24" in out


def test_run_bad_config(tmp_path, capsys):
    cfg = tmp_path / "run.yaml"
    cfg.write_text("odd: odd.yaml\n")
    assert main(["run", str(cfg)]) == 2
    assert "specs" in capsys.readouterr().err
