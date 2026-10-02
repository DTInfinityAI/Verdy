"""Local store: Parquet traces on disk plus a DuckDB index, no server needed.

Layout::

    .verdy/store/
    ├── <sha256>.parquet     traces, content-addressed
    └── index.duckdb         evidence index (schema: verdy/spec/index_v<N>.sql)

Query traces directly from SQL with ``read_parquet('.verdy/store/<sha256>.parquet')``, or
all of them at once with ``read_parquet('.verdy/store/*.parquet', filename = true)``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from verdy.ledger import IntegrityError, sha256_of, verify_report
from verdy.spec import INDEX_SCHEMA_VERSION, load_index_ddl
from verdy.store.base import HistoryRow, IngestResult, Store
from verdy.store.ingest import SUPPORTED_REPORT_VERSIONS, report_rows
from verdy.store.traces import ParquetTraceStore, _pyarrow

DEFAULT_STORE = ".verdy/store"
TABLES = ("meta", "sources", "dim_odd", "dim_policy", "dim_spec", "dim_backend",
          "dim_scenario", "fact_verdict", "fact_rollout", "fact_rollout_spec", "fact_feedback")
DIMENSIONS = ("dim_odd", "dim_policy", "dim_spec", "dim_backend", "dim_scenario")
FACTS_BY_DIGEST = {"fact_verdict": "report_digest", "fact_rollout": "report_digest",
                   "fact_rollout_spec": "report_digest", "fact_feedback": "source_digest",
                   "sources": "report_digest"}
STATUS_RANK = {"FAIL": 0, "INCONCLUSIVE": 1, "PASS": 2}
_READ_ONLY = re.compile(r"^\s*(select|with|from|describe|show|summarize|explain|pivot)\b", re.I)


class StoreError(RuntimeError):
    """Raised for an unusable store, e.g. an index built with another schema version."""


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError('the evidence index needs: pip install "verdy[store]"') from exc
    return duckdb


class LocalStore(Store):
    def __init__(self, root: str | Path = DEFAULT_STORE) -> None:
        _duckdb()  # fail fast, before any run, if the store extra is missing
        _pyarrow()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.traces = ParquetTraceStore(self.root)
        self._con: Any = None

    # -- traces -------------------------------------------------------------------------

    def put_trace(self, trace: dict[str, list[float]]) -> str:
        return self.traces.put(trace)

    def get_trace(self, sha256: str) -> dict[str, list[float]]:
        return self.traces.get(sha256)

    def has_trace(self, sha256: str) -> bool:
        return self.traces.has(sha256)

    # -- database -----------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.root / "index.duckdb"

    @property
    def con(self) -> Any:
        if self._con is None:
            self._con = _duckdb().connect(str(self.db_path))
            self._init_schema()
        return self._con

    def _init_schema(self) -> None:
        con = self._con
        con.execute(load_index_ddl())
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            con.execute("INSERT INTO meta VALUES ('schema_version', ?)",
                        [str(INDEX_SCHEMA_VERSION)])
        elif row[0] != str(INDEX_SCHEMA_VERSION):
            raise StoreError(
                f"the index uses schema v{row[0]} and this Verdy uses "
                f"v{INDEX_SCHEMA_VERSION}; run `verdy index --rebuild` (reports are the "
                "source of truth, so nothing is lost)")

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # -- indexing -------------------------------------------------------------------------

    @staticmethod
    def _expand(paths: Iterable[str | Path]) -> list[Path]:
        out: list[Path] = []
        for p in paths:
            p = Path(p)
            if p.is_dir():
                out += sorted(x for x in p.rglob("*.report.json")
                              if ".verdy" not in x.relative_to(p).parts)
            else:
                out.append(p)
        return out

    def _insert(self, table: str, rows: list[dict[str, Any]], replace: bool = False) -> None:
        if not rows:
            return
        import pyarrow as pa

        arrow = pa.Table.from_pylist(rows)  # noqa: F841  (referenced by name in SQL)
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        self.con.execute(f"{verb} INTO {table} BY NAME SELECT * FROM arrow")

    def _ingest(self, path: Path, result: IngestResult) -> None:
        try:
            report = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.skipped.append((str(path), f"unreadable: {exc}"))
            return
        version = str(report.get("report_version"))
        digest = (report.get("integrity") or {}).get("body_sha256")
        if version not in SUPPORTED_REPORT_VERSIONS or not digest:
            result.skipped.append((str(path), f"unsupported report format {version!r}"))
            return
        try:
            verify_report(report)
        except IntegrityError:
            # Key the record by what the content actually hashes to: a modified copy keeps
            # its original's claimed digest and must never replace the genuine report.
            actual = sha256_of({k: v for k, v in report.items() if k != "integrity"})
            self._insert("sources", [{"report_digest": actual,
                                      "report_path": str(path.resolve()), "valid": False,
                                      "report_version": version}], replace=True)
            result.skipped.append((str(path), "digest mismatch: report was modified"))
            return
        rows = report_rows(report, str(path.resolve()))
        for table, column in FACTS_BY_DIGEST.items():
            self.con.execute(f"DELETE FROM {table} WHERE {column} = ?", [digest])
        for table in DIMENSIONS:
            self._insert(table, rows[table])
        for table in ("sources", "fact_verdict", "fact_rollout", "fact_rollout_spec",
                      "fact_feedback"):
            self._insert(table, rows[table], replace=table == "sources")
        result.indexed.append(str(path))

    def index(self, paths: Iterable[str | Path]) -> IngestResult:
        result = IngestResult()
        self.con.execute("BEGIN TRANSACTION")
        try:
            for path in self._expand(paths):
                self._ingest(path, result)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return result

    def rebuild(self, extra_paths: Iterable[str | Path] = ()) -> IngestResult:
        known: list[str] = []
        if self.db_path.exists():
            con = _duckdb().connect(str(self.db_path))
            try:
                known = [r[0] for r in con.execute("SELECT report_path FROM sources").fetchall()]
            except Exception:  # an index too old or broken to read: start from extra_paths
                known = []
            finally:
                con.close()
            self.close()
            self.db_path.unlink()
        result = IngestResult()
        existing = [p for p in known if Path(p).is_file()]
        result.skipped += [(p, "report no longer exists") for p in known if p not in existing]
        sub = self.index([*existing, *extra_paths])
        result.indexed += sub.indexed
        result.skipped += sub.skipped
        return result

    # -- reading ----------------------------------------------------------------------------

    def query(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        if not _READ_ONLY.match(sql):
            raise StoreError("only read-only queries (SELECT, WITH, DESCRIBE, ...) are allowed")
        cur = self.con.execute(sql, params or [])
        return [d[0] for d in cur.description], cur.fetchall()

    def policies(self) -> list[tuple[str, int, int]]:
        """``(policy name, versions, reports)`` for every indexed policy."""
        return self.con.execute("""
            SELECT p.name, count(DISTINCT p.version), count(*)
            FROM fact_verdict v JOIN dim_policy p USING (policy_key)
            JOIN sources s USING (report_digest) WHERE s.valid
            GROUP BY p.name ORDER BY p.name""").fetchall()

    def history(self, policy: str | None = None) -> list[HistoryRow]:
        rows = self.con.execute("""
            SELECT p.name, p.version, CAST(v.created_at AS VARCHAR), v.status, v.p_fail,
                   v.p_lower, v.p_upper, v.n_runs, v.failures,
                   coalesce(o.name, '?') || ' ' || coalesce(o.version, '?'), s.report_path,
                   v.report_digest, v.suite_label, v.suite_key
            FROM fact_verdict v
            JOIN dim_policy p USING (policy_key)
            JOIN sources s USING (report_digest)
            LEFT JOIN dim_odd o USING (odd_key)
            WHERE s.valid AND (? IS NULL OR p.name = ? OR p.name ILIKE ?)
            ORDER BY p.name, v.suite_label, v.created_at, s.indexed_at, s.report_path""",
            [policy, policy, f"%{policy}%" if policy else None]).fetchall()
        per_spec: dict[str, dict[str, float]] = {}
        for digest, spec, rate in self.con.execute("""
                SELECT r.report_digest, d.name,
                       sum(coalesce(f.weight, 1.0) * r.violated::INT)
                         / nullif(sum(coalesce(f.weight, 1.0)), 0)
                FROM fact_rollout_spec r
                JOIN fact_rollout f USING (report_digest, run_id)
                JOIN dim_spec d USING (spec_key)
                GROUP BY r.report_digest, d.name ORDER BY d.name""").fetchall():
            per_spec.setdefault(digest, {})[spec] = float(rate) if rate is not None else None
        out: list[HistoryRow] = []
        previous: dict[tuple[str, str], HistoryRow] = {}
        for r in rows:
            row = HistoryRow(*r[:12], suite=r[12] or "", per_spec=per_spec.get(r[11], {}))
            key = (row.policy, r[13])  # compare only within the same test suite
            if key in previous:
                row.regression = _regression(previous[key], row)
            previous[key] = row
            out.append(row)
        return out


def _regression(prev: HistoryRow, cur: HistoryRow) -> str | None:
    """Why ``cur`` is worse than ``prev`` (same policy, same suite), or ``None``."""
    if STATUS_RANK.get(cur.status, 1) < STATUS_RANK.get(prev.status, 1):
        return f"verdict {prev.status} -> {cur.status} (vs {prev.version})"
    if cur.p_lower is not None and prev.p_upper is not None and cur.p_lower > prev.p_upper:
        return (f"failure rate significantly higher than {prev.version} (lower bound "
                f"{cur.p_lower:.3g} > its upper bound {prev.p_upper:.3g})")
    return None
