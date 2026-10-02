"""Canonical hashing of inputs and results."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if hasattr(value, "item"):  # numpy scalar
        return _normalize(value.item())
    return value


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, non-finite floats as strings."""
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_of(value: Any) -> str:
    """SHA-256 of the canonical JSON form of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
