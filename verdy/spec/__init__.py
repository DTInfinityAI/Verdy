"""Versioned JSON Schemas published by Verdy."""
import json
from functools import cache
from importlib import resources

ODD_SPEC_VERSION = "0.3.0"
STL_SPEC_VERSION = "0.2.0"
INDEX_SCHEMA_VERSION = 1
TRACE_FORMAT_VERSION = "1"          # single-file traces: <sha256>.parquet
TRACE_BATCH_FORMAT_VERSION = "1"    # batched traces: batches/<batch>.parquet


@cache
def load_schema(name: str) -> dict:
    """Load a bundled schema by name: ``"odd"`` or ``"stl_specs"``."""
    text = resources.files(__package__).joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


def load_index_ddl(version: int = INDEX_SCHEMA_VERSION) -> str:
    """DDL of the evidence index schema (see ``index_v<version>.sql``)."""
    return resources.files(__package__).joinpath(f"index_v{version}.sql").read_text("utf-8")
