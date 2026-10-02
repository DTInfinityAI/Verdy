# Changelog

All notable changes to Verdy are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

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
