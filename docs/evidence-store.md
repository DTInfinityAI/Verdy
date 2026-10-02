# Evidence store and history

Every `verdy run` produces a sealed evidence report. The evidence store adds two things
on top, without changing what a report is:

- **Traces, kept separately.** Every run's trace is saved as Parquet in a
  content-addressed store, at the SHA-256 the report already records for that run.
- **An index.** A DuckDB database derived from reports, for questions that span many
  reports: how did this policy do across releases, which spec regressed, where in the ODD
  do failures concentrate over time?

```bash
pip install -e ".[store]"                       # adds duckdb and pyarrow
cd examples/home_robot
verdy run run.yaml --store                      # home-navigator 1.0.0
verdy run run_tuned.yaml --store                # 1.1.0
verdy run run_v1_2.yaml --store                 # 1.2.0, a "faster" retune
verdy history home-navigator --by-spec
```

```text
Policy home-navigator
  Suite: home-robot-kitchen-crossing 0.1.0 | 3 specs, critical+major | p <= 0.05 @ 0.95
  version        date                verdict        p_fail              bounds   runs     no_collision       reach_goal slow_near_person
  1.0.0          2026-10-02 14:40:18 FAIL           10.10%     [8.57%, 11.81%]   1000            2.00%            0.00%           10.10%
  1.1.0          2026-10-02 14:40:26 PASS            2.80%      [2.00%, 3.82%]   1000            0.80%            0.20%            2.50%
  1.2.0          2026-10-02 14:40:33 INCONCLUSIVE    5.00%      [3.92%, 6.29%]   1000            1.20%            0.00%            4.90%
                 ^ REGRESSION: verdict PASS -> INCONCLUSIVE (vs 1.1.0)

1 regression(s) found.
```

## Principles

| Principle | How |
| --- | --- |
| Reports are the source of truth | The index holds only rows derived from reports. `verdy index --rebuild` regenerates it from them, so the index can never weaken the audit trail. |
| Heavy data stays out of reports | Traces live in the store; reports keep each trace's hash (`runs[].trace_sha256`), as they always have. |
| Content-addressed | A trace's address is the canonical SHA-256 of its values. Identical traces are stored once, and every file can be checked against its name. |
| Versioned schema | The index schema is `verdy/spec/index_v<N>.sql`. An index built with another version is refused until rebuilt. |
| One interface | `verdy.store.Store` hides the implementation: `LocalStore` (files + DuckDB) now, other backends later. |

## Traces

`.verdy/store/<sha256>.parquet` holds one trace: a `time` column and one `float64` column
per signal, with the address and trace format version in the file metadata. Float64
round-trips exactly, so reloading a trace and hashing it reproduces its address;
`LocalStore.get_trace` checks this on every read and rejects a file that does not match.
Files are written atomically.

Query traces directly from DuckDB:

```sql
SELECT max(speed) FROM read_parquet('.verdy/store/<sha256>.parquet');
```

## Storing and indexing

| Command | Does |
| --- | --- |
| `verdy run CONFIG --store [PATH]` | Saves every run's trace to the store (default `.verdy/store`) and indexes the report. Also settable as `store:` in the run config. |
| `verdy index [PATHS...]` | Indexes report files, or directories searched for `*.report.json` (default: `.`). Re-indexing a report replaces its rows. |
| `verdy index --rebuild` | Drops the index and rebuilds it from every report it knew about, plus any paths given. |

Before indexing, each report's digest is checked. A modified report is not indexed: it
is listed as skipped, and recorded as invalid under the digest its contents actually hash
to, so it can never displace the genuine report it was copied from. Reports in an unknown
format are skipped, never misread. Signatures are recorded (`sources.signature_method`)
but not verified by the index; use `verdy verify` with the key.

## Policy versions

History groups reports by policy name and version. Set them in the run config:

```yaml
policy_name: home-navigator
policy_version: 1.1.0
```

or per run, for example from CI: `verdy run run.yaml --store --policy-version "$GIT_SHA"`.
Without them, the name is the policy reference in the config (`policy:make_policy`) and
the version is a short hash of the config's policy section, so different tunings of the
same code count as different versions.

## History and regressions

```bash
verdy history                                   # list indexed policies
verdy history home-navigator                    # verdicts across versions
verdy history home-navigator --by-spec          # plus violation rate of every spec
verdy history home-navigator --json
verdy history home-navigator --fail-on-regression    # exit 1 on a regression (CI)
```

Verdicts are only comparable when they come from the same test, so history groups
reports into **suites**: the same ODD, the same specs and the same verdict rule (target
failure probability, confidence, severities that count, coverage requirement). The
sampler, seed and number of runs may differ. Within a suite, in time order, a report is
flagged as a regression when:

- its verdict is worse than the previous version's (`PASS` → `INCONCLUSIVE` or `FAIL`), or
- its failure rate is significantly higher: its lower bound is above the previous
  version's upper bound.

Per-spec rates are weighted by each run's importance weight, so they stay estimates
under the nominal ODD for importance-sampled reports too.

## The index schema (v1)

A star schema. Dimensions identify *what* was tested; facts record *what happened*.

| Table | One row per | Key columns |
| --- | --- | --- |
| `dim_odd` | ODD version | `odd_key` (ODD SHA-256), `name`, `version` |
| `dim_policy` | policy version | `policy_key`, `name`, `version`, `reference` |
| `dim_spec` | spec definition | `spec_key`, `name`, `formula`, `severity` |
| `dim_backend` | backend configuration | `backend_key`, `name`, `config_json` |
| `dim_scenario` | scenario (params + seed) | `scenario_key`, `params_json`, `seed` |
| `fact_verdict` | report | `report_digest`, `suite_key`, `suite_label`, `status`, `p_fail`, `p_lower`, `p_upper`, `n_runs`, `failures`, `coverage`, keys to every dimension |
| `fact_rollout` | run | `report_digest`, `run_id`, `scenario_key`, `failed`, `weight`, `min_robustness`, `trace_sha256` |
| `fact_rollout_spec` | run and spec | `report_digest`, `run_id`, `spec_key`, `robustness`, `violated` |
| `fact_feedback` | preference pair (improvement loop) | `pair_id`, `run_a`, `run_b`, `choice`, `labeler`, `reason` |
| `sources` | indexed report | `report_digest`, `report_path`, `kind`, `valid`, `signature_method` |

`dim_scenario` is shared: the same seeded scenario evaluated by two policy versions has
one row, so paired comparisons across versions are a join away. Full DDL:
[`verdy/spec/index_v1.sql`](../verdy/spec/index_v1.sql).

## Querying

```bash
verdy query "SELECT p.version, v.status, round(v.p_fail, 4) AS p_fail
             FROM fact_verdict v JOIN dim_policy p USING (policy_key)
             ORDER BY v.created_at"
```

`verdy query` runs read-only SQL (`SELECT`, `WITH`, `DESCRIBE`, ...) and prints a table,
or JSON with `--json`. Some useful queries:

```sql
-- Runs that failed in 1.2.0 but passed the same scenario in 1.1.0
SELECT s.params_json
FROM fact_rollout new
JOIN fact_verdict vn ON vn.report_digest = new.report_digest
JOIN dim_policy pn ON pn.policy_key = vn.policy_key AND pn.version = '1.2.0'
JOIN fact_rollout old ON old.scenario_key = new.scenario_key
JOIN fact_verdict vo ON vo.report_digest = old.report_digest
JOIN dim_policy po ON po.policy_key = vo.policy_key AND po.version = '1.1.0'
JOIN dim_scenario s ON s.scenario_key = new.scenario_key
WHERE new.failed AND NOT old.failed;

-- Failure rate by floor type, across every indexed report
SELECT json_extract_string(s.params_json, '$.floor_type') AS floor, avg(r.failed::INT) AS rate
FROM fact_rollout r JOIN dim_scenario s USING (scenario_key)
GROUP BY floor ORDER BY rate DESC;
```

The database is a plain DuckDB file (`.verdy/store/index.duckdb`), so notebooks and BI
tools can open it directly.

## From Python

```python
import json

from verdy.store import open_store

with open_store(".verdy/store") as store:
    store.index(["reports/"])
    for row in store.history("home-navigator"):
        print(row.version, row.status, row.p_fail, row.regression)
    report = json.load(open(row.report_path))
    trace = store.get_trace(report["runs"][0]["trace_sha256"])   # checked against its address
```

Pass `trace_sink=store.put_trace` to `verdy.evaluate` to store traces from your own
code.

## Limits

- Small Parquet files carry a fixed overhead (roughly 10 KB per trace in the home-robot
  example). For very large campaigns, batching many traces per file is a planned
  optimization; addresses stay the same.
- The local store is single-user: DuckDB allows one writer at a time. Multi-team storage
  is a separate `Store` implementation.
- Sensor-rich runs (images, point clouds) are not covered by the Parquet trace format yet;
  MCAP is the planned format for those.
