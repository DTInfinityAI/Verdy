import copy
import math

import pytest

from verdy.ledger import (
    IntegrityError,
    SignatureError,
    build_report,
    canonical_json,
    read_report,
    sha256_of,
    sign_report,
    verify_report,
    write_report,
)


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [1.5, math.nan]}) == '{"a":[1.5,"NaN"],"b":1}'
    assert sha256_of({"x": 1, "y": 2}) == sha256_of({"y": 2, "x": 1})


@pytest.fixture
def report():
    return build_report(
        inputs={"odd": {"name": "o"}}, runs=[{"id": "s0", "failed": False, "r": math.inf}],
        results={"verdict": {"status": "PASS", "reasons": []}},
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_report_round_trip_and_tamper_detection(tmp_path, report):
    path = write_report(report, tmp_path / "r" / "x.report.json")
    loaded = read_report(path)
    assert verify_report(loaded)["digest_ok"]
    tampered = copy.deepcopy(loaded)
    tampered["results"]["verdict"]["status"] = "FAIL"
    with pytest.raises(IntegrityError):
        verify_report(tampered)


def test_hmac_signature(report):
    sign_report(report, "hmac-sha256", "secret")
    assert verify_report(report, "secret")["signature_checked"]
    with pytest.raises(SignatureError):
        verify_report(report, "wrong")


def test_unsigned_report_with_key(report):
    with pytest.raises(SignatureError, match="not signed"):
        verify_report(report, "secret")


def test_ed25519_signature(tmp_path, report):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv_path, pub_path = tmp_path / "k.pem", tmp_path / "k.pub.pem"
    priv_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub_path.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    sign_report(report, "ed25519", priv_path)
    assert verify_report(report, pub_path)["signature_method"] == "ed25519"
    report["integrity"]["signature"]["value"] = report["integrity"]["signature"]["value"][::-1]
    with pytest.raises(SignatureError):
        verify_report(report, pub_path)
