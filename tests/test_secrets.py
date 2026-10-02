import json
import logging
import os
import pickle

import pytest

from verdy import secrets as S
from verdy.cli import main
from verdy.ledger import build_report

# Built at runtime so the repository never contains a credential-shaped literal.
FAKE_ANTHROPIC = "sk-ant-" + "api03-" + "A1b2C3d4" * 6
FAKE_OPENAI = "sk-" + "proj-" + "Z9y8X7w6" * 5


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "VERDY_FINGERPRINT_KEY",
                 "VERDY_SIGNING_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERDY_SECRETS_FILE", str(tmp_path / "secrets.env"))
    return tmp_path


def test_secret_never_shows_its_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    secret = S.get_secret("ANTHROPIC_API_KEY")
    assert secret.reveal() == FAKE_ANTHROPIC
    for text in (repr(secret), str(secret), f"{secret}", json.dumps(secret.describe())):
        assert FAKE_ANTHROPIC not in text
    assert "sha256:" in repr(secret) and secret.source == "env"
    with pytest.raises(TypeError):
        pickle.dumps(secret)


def test_secrets_file(isolated):
    path = isolated / "secrets.env"
    path.write_text(
        f"# keys\nexport ANTHROPIC_API_KEY='{FAKE_ANTHROPIC}'\nOPENAI_API_KEY={FAKE_OPENAI}\n"
    )
    os.chmod(path, 0o600)
    assert S.get_secret("ANTHROPIC_API_KEY").reveal() == FAKE_ANTHROPIC
    assert S.get_secret("OPENAI_API_KEY").source == "file:secrets.env"
    os.chmod(path, 0o644)
    with pytest.raises(S.SecretError, match="chmod 600"):
        S.get_secret("ANTHROPIC_API_KEY")


def test_env_takes_precedence(isolated, monkeypatch):
    path = isolated / "secrets.env"
    path.write_text("OPENAI_API_KEY=from-file-value-123\n")
    os.chmod(path, 0o600)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    assert S.get_secret("OPENAI_API_KEY").reveal() == FAKE_OPENAI


def test_missing_secret():
    with pytest.raises(S.SecretError, match="ANTHROPIC_API_KEY is not set"):
        S.get_secret("ANTHROPIC_API_KEY")
    assert S.get_secret("ANTHROPIC_API_KEY", required=False) is None


def test_fingerprints(monkeypatch):
    sha = S.fingerprint(FAKE_OPENAI, "sha256")
    assert sha.startswith("sha256:") and len(sha) == len("sha256:") + 16
    assert sha == S.fingerprint(FAKE_OPENAI, "sha256")
    assert S.fingerprint(FAKE_OPENAI, "none") is None
    with pytest.raises(S.SecretError, match="VERDY_FINGERPRINT_KEY"):
        S.fingerprint(FAKE_OPENAI, "hmac")
    a = S.fingerprint(FAKE_OPENAI, "hmac", key="org-key-1")
    assert a.startswith("hmac-sha256:") and a != S.fingerprint(FAKE_OPENAI, "hmac", key="k2")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("VERDY_FINGERPRINT_KEY", "org-key-1")
    assert S.get_secret("OPENAI_API_KEY").fingerprint("hmac") == a
    info = S.describe_secrets(["OPENAI_API_KEY", "GOOGLE_API_KEY"], "hmac")
    assert info == {"GOOGLE_API_KEY": {"set": False},
                    "OPENAI_API_KEY": {"set": True, "source": "env", "fingerprint": a}}


def test_redaction():
    S.REDACTOR.register("custom-internal-token-value")
    text = f"auth failed for {FAKE_ANTHROPIC} / {FAKE_OPENAI} / custom-internal-token-value"
    out = S.redact(text)
    assert FAKE_ANTHROPIC not in out and FAKE_OPENAI not in out
    assert "custom-internal-token-value" not in out and out.count("[REDACTED]") == 3
    nested = S.REDACTOR.scrub({"a": [f"x {FAKE_OPENAI}"], "b": 1.5, "c": (FAKE_ANTHROPIC,)})
    assert nested == {"a": ["x [REDACTED]"], "b": 1.5, "c": ("[REDACTED]",)}


def test_find_credentials_and_scan(tmp_path):
    cfg = {"backend": {"options": {"api_key": FAKE_OPENAI, "dt": 0.1}}, "list": ["ok"]}
    assert S.find_credentials(cfg) == ["backend.options.api_key"]
    clean, dirty = tmp_path / "clean.yaml", tmp_path / "dirty.yaml"
    clean.write_text("secrets: [OPENAI_API_KEY]\n")
    dirty.write_text(f"a: 1\nkey: {FAKE_ANTHROPIC}\n")
    assert S.scan_files([tmp_path]) == [(dirty, 2)]


def test_subprocess_env_is_minimal(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    monkeypatch.setenv("SOME_OTHER_TOKEN", "should-not-leak")
    env = S.subprocess_env(required=["OPENAI_API_KEY"], allow=["GOOGLE_API_KEY"],
                           extra={"WANDB_MODE": "disabled"})
    assert env["OPENAI_API_KEY"] == FAKE_OPENAI and env["WANDB_MODE"] == "disabled"
    assert "ANTHROPIC_API_KEY" not in env and "SOME_OTHER_TOKEN" not in env
    assert "GOOGLE_API_KEY" not in env
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(S.SecretError):
        S.subprocess_env(required=["OPENAI_API_KEY"])


def test_logging_filter(caplog):
    log = logging.getLogger("verdy.test")
    log.addFilter(S.RedactingFilter())
    with caplog.at_level(logging.INFO):
        log.info("calling API with %s", FAKE_ANTHROPIC)
    assert FAKE_ANTHROPIC not in caplog.text and "[REDACTED]" in caplog.text


def test_reports_are_scrubbed():
    report = build_report(inputs={"note": f"key={FAKE_OPENAI}"},
                          runs=[{"error": f"401 for {FAKE_ANTHROPIC}"}], results={})
    text = json.dumps(report)
    assert FAKE_OPENAI not in text and FAKE_ANTHROPIC not in text
    from verdy.ledger import verify_report

    assert verify_report(report)["digest_ok"]


def test_cli_secrets_status_and_scan(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC)
    assert main(["secrets", "status"]) == 0
    out = capsys.readouterr().out
    assert FAKE_ANTHROPIC not in out
    assert "ANTHROPIC_API_KEY" in out and "set (env)" in out and "sha256:" in out
    assert "OPENAI_API_KEY           not set" in out
    (tmp_path / "leak.txt").write_text(f"token {FAKE_OPENAI}\n")
    assert main(["secrets", "scan", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "leak.txt:1" in out and FAKE_OPENAI not in out


def test_cli_errors_are_redacted(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "run.yaml"
    cfg.write_text(f"odd: odd.yaml\nspecs: s.yaml\npolicy: p:x\nbackend: sim2d\n"
                   f"name: {FAKE_OPENAI}\n")
    assert main(["run", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "looks like a credential at: name" in err and FAKE_OPENAI not in err


def test_signing_key_from_secrets_file(isolated, capsys):
    from conftest import EXAMPLES

    path = isolated / "secrets.env"
    path.write_text("VERDY_SIGNING_KEY=team-signing-secret-1234\n")
    os.chmod(path, 0o600)
    report = isolated / "r.report.json"
    code = main(["run", str(EXAMPLES / "home_robot" / "run_tuned.yaml"), "--runs", "20",
                 "-o", str(report), "--sign", "hmac", "-q"])
    assert code in (0, 1, 3)
    assert main(["verify", str(report), "--key-env", "VERDY_SIGNING_KEY"]) == 0
    assert "team-signing-secret-1234" not in report.read_text()
