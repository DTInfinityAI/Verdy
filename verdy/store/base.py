"""The Store interface: where traces live and how reports are indexed.

Reports stay the source of truth. A store keeps heavy artifacts (traces) out of reports,
addressed by the hashes reports already record, and maintains an index derived from
reports that can always be rebuilt from them.

:class:`verdy.store.LocalStore` (filesystem + DuckDB) is the open-source implementation.
Other implementations, such as object storage with a shared warehouse for many teams,
plug in behind the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IngestResult:
    indexed: list[str] = field(default_factory=list)
    """Paths of reports added or refreshed."""
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """``(path, reason)`` for reports that were not indexed."""


@dataclass
class HistoryRow:
    policy: str
    version: str
    created_at: str
    status: str
    p_fail: float | None
    p_lower: float | None
    p_upper: float | None
    n_runs: int
    failures: int
    odd: str
    report_path: str
    report_digest: str
    suite: str = ""
    """The test that produced the verdict (ODD, specs, verdict rule)."""
    per_spec: dict[str, float] = field(default_factory=dict)
    regression: str | None = None
    """Why this report looks worse than the previous one for the same policy, if it does."""


class Store(ABC):
    """Trace storage plus an evidence index."""

    # -- traces ---------------------------------------------------------------------------

    @abstractmethod
    def put_trace(self, trace: dict[str, list[float]]) -> str:
        """Store a trace and return its address (the canonical SHA-256 of the trace)."""

    @abstractmethod
    def get_trace(self, sha256: str) -> dict[str, list[float]]:
        """Load a trace by address. Raises ``KeyError`` if it is not stored."""

    @abstractmethod
    def has_trace(self, sha256: str) -> bool: ...

    def flush(self) -> None:
        """Persist buffered traces. Called by :meth:`close`."""

    # -- index ----------------------------------------------------------------------------

    @abstractmethod
    def index(self, paths: Iterable[str | Path]) -> IngestResult:
        """Index reports (files, or directories searched for ``*.report.json``)."""

    @abstractmethod
    def rebuild(self, extra_paths: Iterable[str | Path] = ()) -> IngestResult:
        """Drop the index and rebuild it from every known report, plus ``extra_paths``."""

    @abstractmethod
    def history(self, policy: str | None = None) -> list[HistoryRow]:
        """Verdicts per policy version over time, with regressions flagged."""

    @abstractmethod
    def query(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        """Run a read-only SQL query on the index; returns column names and rows."""

    def close(self) -> None:
        """Persist buffered traces and release resources."""
        self.flush()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
