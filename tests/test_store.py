import json
import shutil
import sys

import pytest
from conftest import EXAMPLES

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from verdy import VerdictConfig, evaluate  # noqa: E402
from verdy.backends import HomeNavSim  # noqa: E402
from verdy.cli import main  # noqa: E402
from verdy.ledger import build_report, sha256_of, write_report  # noqa: E402
from verdy.metrics import load_specs  # noqa: E402
from verdy.odd import load_odd  # noqa: E402
from verdy.sampler import StratifiedSampler  # noqa: E402
from verdy.store import (  # noqa: E402
    LocalStore,
    ParquetTraceStore,
    StoreError,
    TraceIntegrityError,
    open_store,
)
from verdy.store.ingest import policy_identity, suite_identity  # noqa: E402

HOME = EXAMPLES / "home_robot"
sys.path.insert(0, str(HOME))
from policy import make_policy  # noqa: E402


def good_policy():
    return make_policy(cruise=0.8, slow_radius=2.5, stop_radius=0.9, memory=0.6)

TRACE = {"time": [0.0, 0.1, 0.2], "speed": [0.0, 0.30000000000000004, 1 / 3],
         "dist": [2.0, 1.5, -0.25]}


# -- traces ---------------------------------------------------------------------------------


def test_single_file_layout_is_content_addressed(tmp_path):
    store = ParquetTraceStore(tmp_path, layout="single")
    sha = store.put(TRACE)
    assert sha == sha256_of(TRACE)  # the same hash reports record as trace_sha256
    assert store.path(sha) == tmp_path / f"{sha}.parquet"
    assert store.get(sha) == TRACE  # float64 round-trips exactly
    mtime = store.path(sha).stat().st_mtime_ns
    assert store.put(dict(reversed(list(TRACE.items())))) == sha  # column order irrelevant
    assert store.path(sha).stat().st_mtime_ns == mtime  # stored once
    assert len(list(tmp_path.glob("*.parquet"))) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_single_file_layout_detects_tampering(tmp_path):
    store = ParquetTraceStore(tmp_path, layout="single")
    sha = store.put(TRACE)
    other = store.put({**TRACE, "dist": [9.0, 9.0, 9.0]})
    shutil.copyfile(store.path(other), store.path(sha))
    with pytest.raises(TraceIntegrityError):
        store.get(sha)
    with pytest.raises(KeyError):
        store.get("0" * 64)
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")


# -- indexing --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def home():
    return load_odd(HOME / "odd.yaml"), load_specs(HOME / "specs.yaml")


def evaluate_version(home, store, version, policy, max_p=0.05, n=150, name="nav"):
    odd, specs = home
    result = evaluate(
        odd, specs, HomeNavSim(), policy, StratifiedSampler(odd, seed=3), n,
        VerdictConfig(max_failure_prob=max_p),
        inputs={"policy_name": name, "policy_version": version},
        trace_sink=store.put_trace if store else None)
    return result


def reckless(obs):
    return (obs["max_speed"], 0.0)


@pytest.fixture
def three_versions(home, tmp_path):
    store = LocalStore(tmp_path / "store")
    paths = []
    for version, policy in (("1.0", reckless), ("1.1", good_policy()), ("1.2", reckless)):
        result = evaluate_version(home, store, version, policy, max_p=0.15)
        path = write_report(result.report, tmp_path / "reports" / f"v{version}.report.json")
        store.index([path])
        paths.append((path, result))
    yield store, paths
    store.close()


def test_runs_store_every_trace(three_versions):
    store, paths = three_versions
    for _, result in paths:
        for run in result.report["runs"]:
            assert store.has_trace(run["trace_sha256"])
    run = paths[1][1].report["runs"][0]
    trace = store.get_trace(run["trace_sha256"])
    assert set(trace) == {"time", "speed", "dist_obstacle", "dist_goal", "detected"}


def test_star_schema_counts(three_versions):
    store, paths = three_versions
    cols, rows = store.query("SELECT count(*) FROM fact_verdict")
    assert rows == [(3,)]
    assert store.query("SELECT count(*) FROM fact_rollout")[1] == [(450,)]
    assert store.query("SELECT count(*) FROM fact_rollout_spec")[1] == [(1350,)]
    assert store.query("SELECT count(*) FROM dim_policy")[1] == [(3,)]
    assert store.query("SELECT count(*) FROM dim_spec")[1] == [(3,)]
    # The same seeded scenarios recur across versions: one dimension row each.
    assert store.query("SELECT count(*) FROM dim_scenario")[1] == [(150,)]
    cols, rows = store.query(
        "SELECT p.version, v.status FROM fact_verdict v JOIN dim_policy p USING (policy_key) "
        "ORDER BY p.version")
    assert cols == ["version", "status"]
    statuses = dict(rows)
    assert statuses == {"1.0": "FAIL", "1.1": "PASS", "1.2": "FAIL"}


def test_index_is_idempotent(three_versions):
    store, paths = three_versions
    store.index([p for p, _ in paths])
    assert store.query("SELECT count(*) FROM fact_rollout")[1] == [(450,)]


def test_history_flags_regressions(three_versions):
    store, _ = three_versions
    history = store.history("nav")
    assert [h.version for h in history] == ["1.0", "1.1", "1.2"]
    assert history[0].regression is None and history[1].regression is None
    assert history[2].regression == "verdict PASS -> FAIL (vs 1.1)"
    assert set(history[2].per_spec) == {"no_collision", "slow_near_person", "reach_goal"}
    assert store.policies() == [("nav", 3, 3)]


def test_history_compares_within_a_suite(home, tmp_path):
    store = LocalStore(tmp_path / "store")
    good = good_policy()
    for version, policy, max_p in (("1", good, 0.3), ("2", reckless, 0.9)):
        r = evaluate_version(home, None, version, policy, max_p=max_p, n=60)
        store.index([write_report(r.report, tmp_path / f"v{version}.report.json")])
    rows = store.history("nav")
    assert len({h.suite for h in rows}) == 2  # different verdict rules: different suites
    assert all(h.regression is None for h in rows)
    store.close()


def test_tampered_and_unsupported_reports_are_skipped(three_versions, tmp_path):
    store, paths = three_versions
    data = json.loads(paths[0][0].read_text())
    data["results"]["verdict"]["status"] = "PASS"
    bad = tmp_path / "bad.report.json"
    bad.write_text(json.dumps(data))
    future = tmp_path / "future.report.json"
    future.write_text(json.dumps({"report_version": "99", "integrity": {"body_sha256": "x"}}))
    result = store.index([bad, future])
    assert result.indexed == []
    assert {r for _, r in result.skipped} == {
        "digest mismatch: report was modified", "unsupported report format '99'"}
    assert store.query("SELECT count(*) FROM sources WHERE NOT valid")[1] == [(1,)]
    assert len(store.history("nav")) == 3  # the tampered copy never reaches the facts


def test_rebuild_and_schema_versions(three_versions):
    store, paths = three_versions
    store.con.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    store.close()
    with pytest.raises(StoreError, match="--rebuild"):
        store.query("SELECT 1")
    store.close()
    paths[2][0].unlink()
    result = store.rebuild()
    assert len(result.indexed) == 2
    assert ("report no longer exists" in {r for _, r in result.skipped})
    assert store.query("SELECT count(*) FROM fact_verdict")[1] == [(2,)]


def test_query_is_read_only(three_versions):
    store, _ = three_versions
    for sql in ("DELETE FROM fact_verdict", "DROP TABLE meta", "INSERT INTO meta VALUES (1,2)"):
        with pytest.raises(StoreError):
            store.query(sql)


def test_feedback_from_improvement_loop(tmp_path):
    out = tmp_path / "loop"
    out.mkdir()
    (out / "preferences.jsonl").write_text(json.dumps({
        "pair_id": "c1-s1~s2", "a": "s1", "b": "s2", "choice": "a", "labeler": "maria",
        "features_a": {}, "features_b": {}, "reason": "smoother"}) + "\n")
    report = build_report(inputs={"kind": "improvement_loop"}, runs=[], results={})
    write_report(report, out / "loop.report.json")
    with LocalStore(tmp_path / "store") as store:
        store.index([tmp_path])
        _, rows = store.query("SELECT pair_id, choice, labeler, reason FROM fact_feedback")
        assert rows == [("c1-s1~s2", "a", "maria", "smoother")]
        assert store.query("SELECT kind FROM sources")[1] == [("improvement_loop",)]


def test_policy_and_suite_identity():
    base = {"config": {"policy": {"factory": "policy:make", "args": {"cruise": 1.0}}}}
    name, v1 = policy_identity(base)
    _, v2 = policy_identity({"config": {"policy": {"factory": "policy:make",
                                                   "args": {"cruise": 0.8}}}})
    assert name == "policy:make" and v1 != v2 and v1.startswith("cfg-")
    assert policy_identity({**base, "policy_name": "nav", "policy_version": "2.0"}) == (
        "nav", "2.0")
    a = suite_identity({"specs_sha256": "s", "verdict_config": {"max_failure_prob": 0.05}},
                       "odd")
    b = suite_identity({"specs_sha256": "s", "verdict_config": {"max_failure_prob": 0.01}},
                       "odd")
    assert a[0] != b[0]


def test_open_store():
    with pytest.raises(StoreError, match="s3://"):
        open_store("s3://bucket/verdy")


# -- CLI -------------------------------------------------------------------------------------


def test_cli_store_index_history_query(tmp_path, capsys, monkeypatch):
    work = tmp_path / "home_robot"
    shutil.copytree(HOME, work, ignore=shutil.ignore_patterns("reports", ".verdy"))
    monkeypatch.chdir(work)
    assert main(["run", "run.yaml", "--runs", "80", "--store", "-q"]) in (0, 1, 3)
    assert main(["run", "run_tuned.yaml", "--runs", "80", "--store", "-q"]) in (0, 1, 3)
    assert main(["run", "run_v1_2.yaml", "--runs", "80", "-q",
                 "--policy-version", "1.2.0-rc1"]) in (0, 1, 3)  # not stored yet
    out = capsys.readouterr().out
    assert "Traces stored and report indexed in .verdy/store" in out
    batches = list((work / ".verdy" / "store" / "batches").glob("*.parquet"))
    assert len(batches) == 2  # one batch file per run of 80 traces
    assert not list((work / ".verdy" / "store").glob("*.parquet"))

    assert main(["index", "reports"]) == 0
    assert "Indexed: 3 report(s)" in capsys.readouterr().out

    assert main(["history"]) == 0
    assert "home-navigator" in capsys.readouterr().out
    assert main(["history", "home-navigator", "--by-spec"]) == 0
    out = capsys.readouterr().out
    for version in ("1.0.0", "1.1.0", "1.2.0-rc1"):
        assert version in out
    assert "no_collision" in out and "Suite:" in out
    assert main(["history", "home-navigator", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["version"] for d in data] == ["1.0.0", "1.1.0", "1.2.0-rc1"]

    assert main(["query", "SELECT count(*) AS n FROM fact_verdict"]) == 0
    assert capsys.readouterr().out.split() == ["n", "3"]
    assert main(["query", "DROP TABLE meta"]) == 2
    assert main(["index", "--rebuild"]) == 0
    assert "Rebuilt index: 3 report(s)" in capsys.readouterr().out


def test_cli_history_fails_on_regression(three_versions, capsys):
    store, _ = three_versions
    root = str(store.root)
    store.close()
    assert main(["history", "nav", "--store", root]) == 0
    assert "REGRESSION" in capsys.readouterr().out
    assert main(["history", "nav", "--store", root, "--fail-on-regression"]) == 1


# -- batched traces --------------------------------------------------------------------------


def make_traces(n, offset=0.0):
    return [{"time": [0.0, 0.1, 0.2], "speed": [i + offset, i / 3, 0.1 * i],
             "dist": [2.0, 1.0 / (i + 1), -float(i)]} for i in range(n)]


def test_batched_layout_round_trip(tmp_path):
    store = ParquetTraceStore(tmp_path, batch_size=4, row_group_traces=2)
    traces = make_traces(10)
    shas = [store.put(t) for t in traces]
    assert shas == [sha256_of(t) for t in traces]
    batches = sorted((tmp_path / "batches").glob("*.parquet"))
    assert len(batches) == 2  # two full batches of 4 written; 2 traces still buffered
    assert store.stats().buffered_traces == 2
    assert store.get(shas[-1]) == traces[-1]  # readable from the buffer
    assert store.flush() is not None and store.flush() is None
    fresh = ParquetTraceStore(tmp_path)  # catalog rebuilt from the batch files
    for sha, trace in zip(shas, traces, strict=True):
        assert fresh.has(sha) and fresh.get(sha) == trace
    st = fresh.stats()
    assert (st.batch_files, st.batched_traces, st.single_files) == (3, 10, 0)
    assert not list(tmp_path.glob("*.parquet"))


def test_batches_are_content_addressed_and_deduplicated(tmp_path):
    a, b = ParquetTraceStore(tmp_path / "a"), ParquetTraceStore(tmp_path / "b")
    traces = make_traces(5)
    for t in traces:
        a.put(t)
    for t in reversed(traces):
        b.put(t)
    assert a.flush().name == b.flush().name  # same contents, same batch name
    for t in traces:
        a.put(t)  # already stored: nothing buffered
    assert a.flush() is None
    assert len(list((tmp_path / "a" / "batches").glob("*.parquet"))) == 1


def test_batched_layout_detects_tampering(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    store = ParquetTraceStore(tmp_path)
    sha = store.put(make_traces(1)[0])
    batch = store.flush()
    table = pq.read_table(batch)
    values = table.column("values").to_pylist()
    values[1] = [9.0, 9.0, 9.0]
    pq.write_table(table.set_column(2, "values", pa.array(values,
                                                          type=pa.list_(pa.float64()))), batch)
    with pytest.raises(TraceIntegrityError):
        ParquetTraceStore(tmp_path).get(sha)


def test_compact_packs_single_files(tmp_path):
    single = ParquetTraceStore(tmp_path, layout="single")
    traces = make_traces(7)
    shas = [single.put(t) for t in traces]
    assert len(single.single_files()) == 7
    store = ParquetTraceStore(tmp_path, batch_size=3)
    assert store.compact() == (7, 3)
    assert store.single_files() == []
    fresh = ParquetTraceStore(tmp_path)
    assert [fresh.get(s) for s in shas] == traces
    assert fresh.stats().batch_files == 3
    kept = ParquetTraceStore(tmp_path / "k", layout="single")
    kept.put(traces[0])
    assert ParquetTraceStore(tmp_path / "k").compact(delete=False) == (1, 1)
    assert len(kept.single_files()) == 1


def test_mixed_layouts_and_validation(tmp_path):
    traces = make_traces(2)
    old = ParquetTraceStore(tmp_path, layout="single").put(traces[0])  # Verdy 0.4 store
    store = ParquetTraceStore(tmp_path)
    new = store.put(traces[1])
    store.flush()
    assert store.get(old) == traces[0] and store.get(new) == traces[1]
    assert store.put(traces[0]) == old and store.flush() is None  # already stored
    with pytest.raises(KeyError):
        store.get("f" * 64)
    with pytest.raises(ValueError):
        ParquetTraceStore(tmp_path, layout="zip")
    with pytest.raises(ValueError):
        ParquetTraceStore(tmp_path, batch_size=0)


def test_local_store_close_flushes(tmp_path):
    with LocalStore(tmp_path) as store:
        sha = store.put_trace(make_traces(1)[0])
        assert not (tmp_path / "batches").exists()
    assert LocalStore(tmp_path).has_trace(sha)


def test_cli_store_stats_and_compact(tmp_path, capsys):
    single = ParquetTraceStore(tmp_path, layout="single")
    for t in make_traces(5):
        single.put(t)
    assert main(["store", "stats", "--store", str(tmp_path)]) == 0
    assert "single-file: 5 trace(s)  (run `verdy store compact`" in capsys.readouterr().out
    assert main(["store", "compact", "--store", str(tmp_path), "--batch-size", "2"]) == 0
    assert "Packed 5 single-file trace(s) into 3 batch file(s)" in capsys.readouterr().out
    assert main(["store", "stats", "--store", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "5 traces" in out and "5 traces in 3 batch file(s)" in out
