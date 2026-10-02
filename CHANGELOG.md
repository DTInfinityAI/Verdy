# Changelog

All notable changes to Verdy are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

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
