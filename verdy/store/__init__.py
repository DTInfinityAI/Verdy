"""Trace storage and the evidence index.

Signed reports are the source of truth; the store holds what they point at (traces, by
hash) and an index derived from them that can always be rebuilt. Install with
``pip install "verdy[store]"``.
"""
from __future__ import annotations

from pathlib import Path

from verdy.store.base import HistoryRow, IngestResult, Store
from verdy.store.local import DEFAULT_STORE, LocalStore, StoreError
from verdy.store.traces import ParquetTraceStore, TraceIntegrityError


def open_store(location: str | Path | None = None) -> Store:
    """Open a store by location. Local paths (and ``file://``) give a :class:`LocalStore`.

    Remote stores (``s3://``, warehouses) implement the same :class:`Store` interface and
    are not part of this package.
    """
    loc = str(location or DEFAULT_STORE)
    if loc.startswith("file://"):
        loc = loc[len("file://"):]
    if "://" in loc:
        raise StoreError(f"no store implementation for {loc.split('://')[0]}:// locations; "
                         "this package provides local stores")
    return LocalStore(loc)


__all__ = [
    "DEFAULT_STORE",
    "HistoryRow",
    "IngestResult",
    "LocalStore",
    "ParquetTraceStore",
    "Store",
    "StoreError",
    "TraceIntegrityError",
    "open_store",
]
