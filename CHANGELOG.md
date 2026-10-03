# Changelog

All notable changes to Verdy are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [0.7.0] - 2026-10-03

### Added

- Tree-shaped ontology (ontology spec 0.2.0). Nodes have an id, a label, a one-line
  definition, aliases, a parent and a status (`draft` or `approved`). Leaves are parameters
  with units, bounds, a distribution and grounding. Roots are the ODD categories. The
  bundled `core` ontology is now a tree of 31 parameters in 18 groups. Flat 0.6 ontologies
  still load.
- `verdy ontology validate` enforces at most 15 children per node (`max_children`), which
  leaves room for "none" in Laya's option budget. CI runs it on the core ontology.
- `verdy ontology render` generates an LLM skill from the ontology: `SKILL.md` (naming
  conventions, units, distributions, grounding, branches, worked examples) and
  `references/<branch>.md`. The rendered core skill is committed in
  `skills/ontology-core/`, and CI fails if it is stale. `verdy author` sends `SKILL.md` with
  extraction and only the touched branches' references with miss authoring, and records
  `skill_sha256` in the ODD.
- `laya-tree` resolver: Laya walks the tree level by level with a beam (keeps the
  runner-up when close), the path probability is the product of its steps, and "none"
  below a group becomes the new entry's placement (`resolution.path`,
  `resolution.placement`, `provenance.ontology_parent`). Loads fine-tuned checkpoints from
  a directory.
- Approval log (`verdy ontology log`, also written by `verdy ontology add`): phrase → leaf
  records with the resolver's original decision. New entries are logged as "none" at their
  group, with a snapshot of the options shown at the time. `verdy ontology paraphrase` adds
  synthetic paraphrases tagged `source: synthetic`.
- Laya fine-tuning (`verdy.finetune`, see `docs/laya-finetuning.md`):
  - `verdy laya dataset`: one example per tree level from the current tree, with a stable
    train/held-out split, held-out data from human approvals only, and no paraphrase of a
    held-out phrase in training.
  - `verdy laya items`: tokenizes rows into the training-item format of Laya's fine-tuning
    script.
  - `verdy laya eval`: a promotion gate on per-level accuracy and calibration.
  - `verdy laya due`: says when retraining is due.

### Changed

- ODD spec 0.4.0 (additive): `provenance.ontology_parent`, and `resolution.path`,
  `resolution.placement` and `resolution.unit`.
- Miss authoring places each new entry under an existing group.

## [0.6.0] - 2026-10-03

### Added

- Ontology-resolved ODD authoring. `verdy author` runs LLM → shortlist → resolver → LLM:
  Claude extracts candidate parameters with ranges, an embedding shortlist keeps the top-k
  ontology entries per candidate, a resolver picks the matching entry or `none` with a
  probability, and Claude writes definitions only for the misses (flagged
  `new_ontology_entry`). Matched parameters take the ontology's name, unit, bounds,
  distribution and grounding. See [docs/ontology.md](docs/ontology.md).
- Resolver plug-ins (`verdy.odd.resolve`): `exact` (string match on names and synonyms),
  `laya` (the open-weight Laya decision model run locally, `pip install "verdy[laya]"`, no
  API key), `llm` (Claude, one call for all candidates), or `module:attribute`.
- Parameter ontologies (`verdy.odd.ontology`): a versioned schema
  (`verdy schema ontology`), a bundled `core` ontology with 31 entries, and
  `verdy ontology list` / `verdy ontology add` to grow the ontology from approved ODDs.
- Shortlist embedders: `hashing` (local, no dependencies) or
  `sentence-transformers[:MODEL]`.

### Changed

- ODD spec 0.3.0 (additive): provenance `source: ontology`, `resolution` (resolver, model,
  decision, probability, phrase, shortlist, ontology), `new_ontology_entry` and
  `also_mentioned_as`. Reports embed the ODD, so the evidence shows why "murky water"
  became `turbidity`.
- Parameters with `source: ontology` need human approval, like `source: llm`.
- `verdy author` resolves against the `core` ontology with the `exact` resolver by default;
  `--ontology none` restores the 0.5 behaviour (Claude writes every parameter).

## [0.5.0] - 2026-10-02

### Added

- Batched trace files: many traces per Parquet file in `.verdy/store/batches/`, one row per
  trace and signal, sorted by address with small row groups, content-addressed batch
  names. Addresses are unchanged (`trace_sha256`), so reports and the index need no
  change. In the home-robot example: 3 files and 7.5 MB instead of 2,962 files holding
  16.4 MB (35 MB of disk blocks).
- `verdy store stats` and `verdy store compact` (packs Verdy 0.4 single-file traces into
  batches, verifying every trace before deleting its file).
- `Store.flush()`; `verdy run --store` writes traces before the report that references them.

### Changed

- The trace store writes batches by default; single-file traces remain readable and can
  still be written with `layout="single"`.

## [0.4.0] - 2026-10-02

### Added

- Evidence store (`pip install "verdy[store]"`, `verdy.store`): traces saved as Parquet in
  a content-addressed store at the `trace_sha256` reports already record, verified on
  read; a `Store` interface with a local filesystem + DuckDB implementation.
- Evidence index: a versioned star schema (`verdy/spec/index_v1.sql`) derived from
  reports, with dimensions for ODD, policy version, spec, backend and scenario, and facts
  for verdicts, rollouts, per-spec robustness and feedback. Rebuildable from reports;
  modified reports are rejected and cannot displace genuine ones.
- `verdy run --store`, `verdy index [--rebuild]`, `verdy history` (verdicts per policy
  version, grouped by test suite, regressions flagged, `--fail-on-regression` for CI) and
  `verdy query`.
- Run config keys `policy_name`, `policy_version` and `store`; `--policy-name` and
  `--policy-version` on `verdy run`.
- `trace_sink` option on `verdy.evaluate`.
- Home-robot release 1.2.0 config for the history demo.

## [0.3.0] - 2026-10-02

### Added

- Improvement loop (`verdy improve`, `verdy.improve`): diagnose, collect feedback, build
  a failure-focused curriculum, train, and re-certify candidate and incumbent on fresh
  held-out scenarios, promoting only when the candidate is no worse (with optional
  no-regression guards on specs such as task completion). Writes a sealed loop report
  linking every evidence report.
- Safety-margin reward from STL robustness, and a bounded preference reward.
- Human feedback: pair selection, file, CLI and scripted labelers, and a Bradley-Terry
  reward model over run features.
- Expert demonstrations: recording and loading, and behavior-cloning pretraining.
- Plug-in trainers: built-in parameter search, an external-command trainer that exports
  each cycle's data for any RL/RLHF framework, or any Python class.
- `FixedScenarioSampler`.
- Home-robot improvement example with a simulated operator and recorded sessions.
- README: SceneSmith setup steps and the improvement loop.

### Changed

- Modules loaded from run configs can import modules next to them.

## [0.2.0] - 2026-10-02

### Added

- SceneSmith integration (`backend: scenesmith`): per-scenario scene prompts, scene
  generation with SceneSmith (cached), the policy contract from SceneSmith's robot
  evaluation, and task validation with SceneSmith's validator, exposed as `task_score`,
  `task_success` and `requirements_met` signals.
- Scene prompt writers: template, and Claude (cached, recorded in the report).
- `verdy.secrets`: keys from environment variables or a private secrets file; `Secret`
  values that never print; fingerprints (`sha256`, `hmac`, `none`) in reports; redaction
  of logs, errors, subprocess output and reports; minimal subprocess environments.
- `verdy secrets status` and `verdy secrets scan`; CI scans the repository for keys.
- Run configs: `credentials.fingerprint`; configs containing credentials are rejected.
- Reports record backend settings, per-run backend details and credential fingerprints.
- SceneSmith pick-and-place example.

### Changed

- The step-by-step simulator bridge is now `SceneClientBackend`; `SceneSmithBackend` is
  the SceneSmith pipeline.
- HMAC signing and verification keys are read through `verdy.secrets`.
- Python modules referenced from run configs are loaded from the config's directory by
  path, so two configs with a same-named `policy.py` no longer collide.

## [0.1.0] - 2026-10-02

### Added

- ODD meta-schema `0.2.0` with semantic validation, safe constraint expressions, nominal
  distributions and provenance tracking.
- LLM-assisted ODD authoring with Claude (`verdy author`, optional `llm` extra).
- STL safety-spec files with schema validation and RTAMT robustness scoring.
- Samplers: Monte Carlo, stratified (Latin hypercube), cross-entropy importance sampling
  with a defensive mixture, and log replay.
- Backends: built-in 2D home-robot simulator, log replay, SceneSmith bridge, and a
  function adapter for custom simulators and HIL rigs.
- Statistical verdicts: Clopper-Pearson and weighted failure-probability bounds, ODD
  coverage, and the PASS / FAIL / INCONCLUSIVE decision rule.
- Evidence ledger: canonical digests, HMAC-SHA256 and Ed25519 signatures, verification.
- Runtime monitors using past-time STL.
- `verdy` command-line tool: `validate`, `sample`, `run`, `verify`, `plan`, `author`,
  `schema`.
- Home-robot and log-replay examples, documentation, tests, and CI.

### Changed

- Packaging moved to the repository root; `pip install .` now works.
