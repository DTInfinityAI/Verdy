"""Read and write ODD documents as YAML or JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from verdy.odd.model import ODD
from verdy.odd.validation import validate_odd


def read_document(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON file into a dict."""
    path = Path(path)
    text = path.read_text("utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def load_odd(path: str | Path, *, strict: bool = False) -> ODD:
    """Load and validate an ODD file."""
    return validate_odd(read_document(path), strict=strict)


def save_odd(odd: ODD, path: str | Path) -> None:
    """Write an ODD as YAML (``.yaml``/``.yml``) or JSON (anything else)."""
    path = Path(path)
    data = odd.to_dict()
    if path.suffix.lower() in (".yaml", ".yml"):
        path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    else:
        path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
