"""Content-addressed traces in Parquet, batched many traces per file.

Every trace is addressed by its canonical SHA-256 (:func:`verdy.ledger.sha256_of`): the
same ``trace_sha256`` every evidence report already records for each run. Reports need
no change to point at their traces, identical traces are stored once, and every trace
can be checked by reloading it and recomputing its address (float64 round-trips
exactly, so the hash survives storage).

Two layouts share one address space:

``batches/<batch>.parquet`` (default)
    Many traces per file, one row per trace and signal: ``trace_sha256`` (string),
    ``signal`` (string) and ``values`` (list of float64; the ``time`` signal holds the
    time stamps). Rows are sorted by address and written in small row groups, so one
    trace is read without scanning the whole file. A batch is named after the SHA-256 of
    the addresses it holds, so batch files are content-addressed too.

``<sha256>.parquet``
    One trace per file, columns ``time`` and one per signal. Written by Verdy 0.4 or with
    ``layout="single"``; always readable, and ``compact()`` packs them into batches.

Which batch holds a trace is worked out from the batch files themselves (their
``trace_sha256`` column), so there is no extra catalog to keep in sync.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verdy.ledger import sha256_of
from verdy.spec import TRACE_BATCH_FORMAT_VERSION, TRACE_FORMAT_VERSION

_SHA = re.compile(r"^[0-9a-f]{64}$")
LAYOUTS = ("batched", "single")


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError('the trace store needs: pip install "verdy[store]"') from exc
    return pa, pq


class TraceIntegrityError(ValueError):
    """Raised when a stored trace does not match its address."""


@dataclass
class TraceStoreStats:
    single_files: int
    batch_files: int
    batched_traces: int
    buffered_traces: int
    bytes_on_disk: int

    @property
    def traces(self) -> int:
        return self.single_files + self.batched_traces + self.buffered_traces


def _atomic_write(table: Any, target: Path, compression: str, **kwargs: Any) -> None:
    _, pq = _pyarrow()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp, compression=compression, **kwargs)
        os.replace(tmp, target)  # readers never see a partial file
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class ParquetTraceStore:
    """Content-addressed trace storage.

    Args:
        root: store directory.
        layout: ``"batched"`` (default) or ``"single"`` (one file per trace).
        batch_size: traces buffered before a batch file is written. Call :meth:`flush`
            (or close the owning store) to write a partial batch.
        row_group_traces: traces per Parquet row group inside a batch; smaller groups make
            single-trace reads cheaper, larger ones compress better.
    """

    def __init__(self, root: str | Path, compression: str = "zstd", layout: str = "batched",
                 batch_size: int = 1000, row_group_traces: int = 64) -> None:
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        if batch_size < 1 or row_group_traces < 1:
            raise ValueError("batch_size and row_group_traces must be at least 1")
        self.root = Path(root)
        self.compression = compression
        self.layout = layout
        self.batch_size = batch_size
        self.row_group_traces = row_group_traces
        self._buffer: dict[str, dict[str, list[float]]] = {}
        self._catalog: dict[str, Path] = {}
        self._catalog_files: set[str] = set()

    @property
    def batch_dir(self) -> Path:
        return self.root / "batches"

    def path(self, sha256: str) -> Path:
        """Path of the single-file form of a trace (whether or not it exists)."""
        if not _SHA.match(sha256):
            raise ValueError(f"not a SHA-256 address: {sha256!r}")
        return self.root / f"{sha256}.parquet"

    # -- catalog ----------------------------------------------------------------------------

    def _batch_files(self) -> list[Path]:
        return sorted(self.batch_dir.glob("*.parquet")) if self.batch_dir.is_dir() else []

    def _refresh_catalog(self) -> None:
        _, pq = _pyarrow()
        for f in self._batch_files():
            if f.name in self._catalog_files:
                continue
            column = pq.read_table(f, columns=["trace_sha256"]).column("trace_sha256")
            for sha in set(column.to_pylist()):
                self._catalog.setdefault(sha, f)
            self._catalog_files.add(f.name)

    def batch_of(self, sha256: str) -> Path | None:
        """The batch file holding a trace, if it is batched."""
        self.path(sha256)  # validates the address
        if sha256 not in self._catalog:
            self._refresh_catalog()
        return self._catalog.get(sha256)

    # -- writing ----------------------------------------------------------------------------

    def has(self, sha256: str) -> bool:
        return (sha256 in self._buffer or self.path(sha256).is_file()
                or self.batch_of(sha256) is not None)

    def put(self, trace: dict[str, list[float]]) -> str:
        """Store a trace (buffered in batched layout); return its address."""
        sha = sha256_of(trace)
        if self.has(sha):
            return sha  # content-addressed: identical traces are stored once
        if self.layout == "single":
            self._write_single(sha, trace)
            return sha
        self._buffer[sha] = {k: [float(x) for x in v] for k, v in trace.items()}
        if len(self._buffer) >= self.batch_size:
            self.flush()
        return sha

    def _write_single(self, sha: str, trace: dict[str, list[float]]) -> None:
        pa, _ = _pyarrow()
        columns = {"time": trace["time"], **{k: v for k, v in trace.items() if k != "time"}}
        table = pa.table({k: pa.array(v, type=pa.float64()) for k, v in columns.items()})
        table = table.replace_schema_metadata({
            "verdy.trace_sha256": sha, "verdy.trace_format": TRACE_FORMAT_VERSION})
        _atomic_write(table, self.path(sha), self.compression)

    def flush(self) -> Path | None:
        """Write buffered traces as one batch file; return its path (``None`` if empty)."""
        if not self._buffer:
            return None
        path = self._write_batch(self._buffer)
        self._buffer = {}
        return path

    def _write_batch(self, traces: dict[str, dict[str, list[float]]]) -> Path:
        pa, _ = _pyarrow()
        shas, signals, values = [], [], []
        rows_per_trace = []
        for sha in sorted(traces):
            trace = traces[sha]
            names = ["time", *sorted(k for k in trace if k != "time")]
            rows_per_trace.append(len(names))
            for name in names:
                shas.append(sha)
                signals.append(name)
                values.append(trace[name])
        table = pa.table({
            "trace_sha256": pa.array(shas, type=pa.string()),
            "signal": pa.array(signals, type=pa.string()),
            "values": pa.array(values, type=pa.list_(pa.float64())),
        }).replace_schema_metadata({
            "verdy.trace_batch_format": TRACE_BATCH_FORMAT_VERSION,
            "verdy.trace_count": str(len(traces)),
        })
        name = hashlib.sha256("\n".join(sorted(traces)).encode()).hexdigest()
        target = self.batch_dir / f"{name}.parquet"
        avg_rows = max(1, round(sum(rows_per_trace) / len(rows_per_trace)))
        _atomic_write(table, target, self.compression,
                      row_group_size=self.row_group_traces * avg_rows,
                      use_dictionary=["trace_sha256", "signal"],
                      write_statistics=["trace_sha256"])
        for sha in traces:
            self._catalog[sha] = target
        self._catalog_files.add(target.name)
        return target

    # -- reading ----------------------------------------------------------------------------

    def get(self, sha256: str, verify: bool = True) -> dict[str, list[float]]:
        if sha256 in self._buffer:
            trace = {k: list(v) for k, v in self._buffer[sha256].items()}
        elif self.path(sha256).is_file():
            _, pq = _pyarrow()
            table = pq.read_table(self.path(sha256))
            trace = {name: table.column(name).to_pylist() for name in table.column_names}
        else:
            batch = self.batch_of(sha256)
            if batch is None:
                raise KeyError(sha256)
            trace = self._read_from_batch(batch, sha256)
        if verify and sha256_of(trace) != sha256:
            raise TraceIntegrityError(f"trace {sha256[:12]}... does not match its address")
        return trace

    @staticmethod
    def _read_from_batch(batch: Path, sha256: str) -> dict[str, list[float]]:
        _, pq = _pyarrow()
        table = pq.read_table(batch, columns=["signal", "values"],
                              filters=[("trace_sha256", "=", sha256)])
        trace = dict(zip(table.column("signal").to_pylist(),
                         table.column("values").to_pylist(), strict=True))
        if not trace:
            raise KeyError(sha256)
        return trace

    # -- maintenance ------------------------------------------------------------------------

    def single_files(self) -> list[Path]:
        return sorted(p for p in self.root.glob("*.parquet") if _SHA.match(p.stem))

    def compact(self, delete: bool = True) -> tuple[int, int]:
        """Pack single-file traces into batches. Returns ``(traces, batches)`` written.

        Each trace is verified before it is packed and read back from its batch before
        its single file is deleted, so a failure never loses data.
        """
        singles = self.single_files()
        written_batches = 0
        for start in range(0, len(singles), self.batch_size):
            group = singles[start : start + self.batch_size]
            traces = {p.stem: self.get(p.stem) for p in group}
            batch = self._write_batch(traces)
            written_batches += 1
            for sha in traces:
                if self._read_from_batch(batch, sha) != traces[sha]:
                    raise TraceIntegrityError(f"verification failed for {sha} in {batch}")
            if delete:
                for p in group:
                    p.unlink()
        return len(singles), written_batches

    def stats(self) -> TraceStoreStats:
        self._refresh_catalog()
        files: Iterable[Path] = [*self.single_files(), *self._batch_files()]
        return TraceStoreStats(
            single_files=len(self.single_files()),
            batch_files=len(self._batch_files()),
            batched_traces=len(self._catalog),
            buffered_traces=len(self._buffer),
            bytes_on_disk=sum(f.stat().st_size for f in files),
        )
