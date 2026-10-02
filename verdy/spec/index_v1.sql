-- Verdy evidence index, schema version 1.
--
-- A star schema derived entirely from evidence reports: every row can be rebuilt from the
-- reports (`verdy index --rebuild`), so the index never weakens the audit trail. Signed
-- reports remain the source of truth.
--
-- Dimensions: ODD version, policy version, spec, backend, scenario.
-- Facts: verdicts (one row per report), rollouts (one row per run), rollout_spec (one
-- row per run and spec, with robustness), feedback (one row per preference pair).

CREATE TABLE IF NOT EXISTS meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);

CREATE TABLE IF NOT EXISTS sources (
    report_digest VARCHAR PRIMARY KEY,   -- integrity.body_sha256
    report_path VARCHAR,
    kind VARCHAR,                        -- evaluation | improvement_loop
    report_version VARCHAR,
    valid BOOLEAN,                       -- digest matched when indexed
    signature_method VARCHAR,            -- present in the report; not verified here
    indexed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_odd (
    odd_key VARCHAR PRIMARY KEY,         -- inputs.odd_sha256
    name VARCHAR,
    version VARCHAR,
    n_parameters INTEGER
);

CREATE TABLE IF NOT EXISTS dim_policy (
    policy_key VARCHAR PRIMARY KEY,      -- sha256(name, version)
    name VARCHAR,
    version VARCHAR,
    reference VARCHAR                    -- inputs.policy (module:attribute)
);

CREATE TABLE IF NOT EXISTS dim_spec (
    spec_key VARCHAR PRIMARY KEY,        -- sha256 of the spec definition
    name VARCHAR,
    formula VARCHAR,
    severity VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_backend (
    backend_key VARCHAR PRIMARY KEY,     -- sha256(name, config)
    name VARCHAR,
    config_json VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_scenario (
    scenario_key VARCHAR PRIMARY KEY,    -- sha256(params, seed): same scenario across reports
    params_json VARCHAR,
    seed BIGINT
);

CREATE TABLE IF NOT EXISTS fact_verdict (
    report_digest VARCHAR PRIMARY KEY,
    created_at TIMESTAMP,
    verdy_version VARCHAR,
    suite_key VARCHAR,                   -- sha256(ODD, specs, verdict rule): verdicts with
    suite_label VARCHAR,                 --   the same suite key are directly comparable
    odd_key VARCHAR,
    policy_key VARCHAR,
    backend_key VARCHAR,
    sampler VARCHAR,
    seed BIGINT,
    status VARCHAR,                      -- PASS | FAIL | INCONCLUSIVE
    n_runs INTEGER,
    n_errors INTEGER,
    failures INTEGER,
    p_fail DOUBLE,
    p_lower DOUBLE,
    p_upper DOUBLE,
    confidence DOUBLE,
    max_failure_prob DOUBLE,
    method VARCHAR,
    coverage DOUBLE,
    pairwise_coverage DOUBLE
);

CREATE TABLE IF NOT EXISTS fact_rollout (
    report_digest VARCHAR,
    run_id VARCHAR,
    scenario_key VARCHAR,
    failed BOOLEAN,
    errored BOOLEAN,
    weight DOUBLE,
    duration_s DOUBLE,
    min_robustness DOUBLE,
    trace_sha256 VARCHAR,                -- address in the trace store
    PRIMARY KEY (report_digest, run_id)
);

CREATE TABLE IF NOT EXISTS fact_rollout_spec (
    report_digest VARCHAR,
    run_id VARCHAR,
    spec_key VARCHAR,
    robustness DOUBLE,
    violated BOOLEAN,
    PRIMARY KEY (report_digest, run_id, spec_key)
);

CREATE TABLE IF NOT EXISTS fact_feedback (
    pair_id VARCHAR,
    source_digest VARCHAR,               -- the improvement-loop report it came with
    run_a VARCHAR,
    run_b VARCHAR,
    choice VARCHAR,                      -- a | b | tie
    labeler VARCHAR,
    reason VARCHAR,
    PRIMARY KEY (source_digest, pair_id)
);
