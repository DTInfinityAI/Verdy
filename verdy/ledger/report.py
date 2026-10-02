"""Evidence reports: everything needed to audit and reproduce a verdict."""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from verdy.ledger.hashing import canonical_json, sha256_of
from verdy.ledger.signing import SignatureError, sign_digest, verify_signature
from verdy.secrets import REDACTOR

REPORT_VERSION = "1"


class IntegrityError(ValueError):
    """Raised when a report's contents do not match its recorded digest."""


def environment_info() -> dict[str, Any]:
    packages = {}
    for name in ("verdy", "numpy", "scipy", "rtamt", "jsonschema"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": packages,
    }


def build_report(
    *,
    inputs: dict[str, Any],
    runs: list[dict[str, Any]],
    results: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Assemble a report and seal it with a digest over its body.

    Every string is passed through the secrets redactor first, so API keys that slip into
    an error message or a config can never be written to a report.
    """
    from verdy import __version__

    body = {
        "report_version": REPORT_VERSION,
        "verdy_version": __version__,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment_info(),
        "inputs": inputs,
        "runs": runs,
        "results": results,
    }
    body = REDACTOR.scrub(json.loads(canonical_json(body)))  # no credential ever lands here
    return {**body, "integrity": {"body_sha256": sha256_of(body)}}


def _body(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k != "integrity"}


def sign_report(report: dict[str, Any], method: str, key: bytes | str | Path) -> dict[str, Any]:
    """Add a signature over the report digest (in place) and return the report."""
    digest = report["integrity"]["body_sha256"]
    report["integrity"]["signature"] = sign_digest(digest, method, key)
    return report


def verify_report(report: dict[str, Any], key: bytes | str | Path | None = None) -> dict[str, Any]:
    """Check a report's digest, and its signature when ``key`` is given.

    Returns a summary dict. Raises :class:`IntegrityError` or ``SignatureError``.
    """
    integrity = report.get("integrity") or {}
    digest = sha256_of(_body(report))
    if digest != integrity.get("body_sha256"):
        raise IntegrityError("report contents do not match the recorded digest")
    signature = integrity.get("signature")
    signed = False
    if key is not None:
        if not signature:
            raise SignatureError("report is not signed")
        verify_signature(digest, signature, key)
        signed = True
    return {
        "digest": digest,
        "digest_ok": True,
        "signature_checked": signed,
        "signature_method": signature.get("method") if signature else None,
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return path


def read_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text("utf-8"))
