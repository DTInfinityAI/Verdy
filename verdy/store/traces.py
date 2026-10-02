"""Content-addressed trace files in Parquet.

A trace is stored at ``<root>/<sha256>.parquet``, where ``sha256`` is the canonical
SHA-256 of the trace (:func:`verdy.ledger.sha256_of`): the same ``trace_sha256`` every
evidence report already records for each run. So a report needs no change to point at
its traces, identical traces are stored once, and any file can be checked by reloading it
and recomputing its address. Columns are ``time`` and one ``float64`` column per signal;
float64 round-trips exactly, so the hash survives storage.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from verdy.ledger import sha256_of
from verdy.spec import TRACE_FORMAT_VERSION

_SHA = re.compile(r"^[0-9a-f]{64}$")


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError('the trace store needs: pip install "verdy[store]"') from exc
    return pa, pq


class TraceIntegrityError(ValueError):
    """Raised when a stored trace does not match its address."""


class ParquetTraceStore:
    def __init__(self, root: str | Path, compression: str = "zstd") -> None:
        self.root = Path(root)
        self.compression = compression

    def path(self, sha256: str) -> Path:
        if not _SHA.match(sha256):
            raise ValueError(f"not a SHA-256 address: {sha256!r}")
        return self.root / f"{sha256}.parquet"

    def has(self, sha256: str) -> bool:
        return self.path(sha256).is_file()

    def put(self, trace: dict[str, list[float]]) -> str:
        """Write a trace if it is not stored yet; return its address."""
        pa, pq = _pyarrow()
        sha = sha256_of(trace)
        target = self.path(sha)
        if target.is_file():
            return sha  # content-addressed: identical traces are stored once
        self.root.mkdir(parents=True, exist_ok=True)
        columns = {"time": trace["time"], **{k: v for k, v in trace.items() if k != "time"}}
        table = pa.table({k: pa.array(v, type=pa.float64()) for k, v in columns.items()})
        table = table.replace_schema_metadata({
            "verdy.trace_sha256": sha, "verdy.trace_format": TRACE_FORMAT_VERSION})
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        os.close(fd)
        try:
            pq.write_table(table, tmp, compression=self.compression)
            os.replace(tmp, target)  # atomic: readers never see a partial file
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return sha

    def get(self, sha256: str, verify: bool = True) -> dict[str, list[float]]:
        _, pq = _pyarrow()
        path = self.path(sha256)
        if not path.is_file():
            raise KeyError(sha256)
        table = pq.read_table(path)
        trace = {name: table.column(name).to_pylist() for name in table.column_names}
        if verify and sha256_of(trace) != sha256:
            raise TraceIntegrityError(f"{path.name} does not match its address")
        return trace
