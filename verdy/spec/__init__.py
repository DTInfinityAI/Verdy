"""Versioned JSON Schemas published by Verdy."""
import json
from functools import cache
from importlib import resources

ODD_SPEC_VERSION = "0.2.0"
STL_SPEC_VERSION = "0.2.0"


@cache
def load_schema(name: str) -> dict:
    """Load a bundled schema by name: ``"odd"`` or ``"stl_specs"``."""
    text = resources.files(__package__).joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)
