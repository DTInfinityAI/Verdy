"""Signatures over report digests.

Two methods are supported:

* ``hmac-sha256``: a shared secret (bytes). Built in. Anyone holding the secret can both
  sign and verify, so use it inside one organization.
* ``ed25519``: a private key signs, the public key verifies. Needs
  ``pip install "verdy[sign]"``. Keys are PEM files.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
from typing import Any


class SignatureError(ValueError):
    """Raised when a signature is missing, malformed or does not verify."""


def sign_digest(digest: str, method: str, key: bytes | str | Path) -> dict[str, Any]:
    """Sign a hex digest and return the signature record stored in the report."""
    if method == "hmac-sha256":
        secret = key.encode("utf-8") if isinstance(key, str) else bytes(key)  # type: ignore[arg-type]
        mac = hmac.new(secret, digest.encode("ascii"), hashlib.sha256).hexdigest()
        return {"method": method, "value": mac}
    if method == "ed25519":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise SignatureError("key is not an Ed25519 private key")
        sig = private.sign(digest.encode("ascii"))
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return {
            "method": method,
            "value": base64.b64encode(sig).decode("ascii"),
            "public_key": base64.b64encode(public).decode("ascii"),
        }
    raise SignatureError(f"unknown signing method {method!r}")


def verify_signature(digest: str, signature: dict[str, Any], key: bytes | str | Path) -> None:
    """Raise :class:`SignatureError` unless ``signature`` is valid for ``digest``.

    ``key`` is the shared secret for HMAC, or the public key PEM path for Ed25519.
    """
    method = signature.get("method")
    if method == "hmac-sha256":
        expected = sign_digest(digest, method, key)["value"]
        if not hmac.compare_digest(expected, str(signature.get("value", ""))):
            raise SignatureError("HMAC signature does not match")
        return
    if method == "ed25519":
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public = serialization.load_pem_public_key(Path(key).read_bytes())
        if not isinstance(public, Ed25519PublicKey):
            raise SignatureError("key is not an Ed25519 public key")
        try:
            public.verify(base64.b64decode(signature["value"]), digest.encode("ascii"))
        except (InvalidSignature, KeyError, ValueError) as exc:
            raise SignatureError("Ed25519 signature does not verify") from exc
        return
    raise SignatureError(f"unknown signing method {method!r}")
