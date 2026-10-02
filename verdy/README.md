# Verdy

**A driving test for robot AI.**

Verdy tells you whether a robot policy is safe to deploy, and how confident you can be. Describe your operating conditions, and Verdy samples scenarios across that domain, runs your policy in simulation or on recorded logs, scores every run against formal safety specs, and returns a **PASS / FAIL / INCONCLUSIVE** verdict with confidence bounds and a reproducible evidence trail.

## Key features
- **ODD specs:** describe operating domains in a typed, versioned schema (LLM-assisted authoring)
- **Scenario sampling:** coverage, log replay, and failure-seeking importance sampling
- **Backend-agnostic:** SceneSmith, custom simulators, log replay, HIL
- **STL scoring:** robustness margins, not just pass/fail
- **Statistical verdicts:** failure-probability bounds and coverage reports
- **Runtime monitors:** the same specs, deployed on the robot

## Layout
| Folder | Purpose |
|---|---|
| `spec/` | ODD meta-schema (JSON Schema) |
| `odd/` | ODD parsing, validation, LLM authoring |
| `sampler/` | Scenario generation strategies |
| `backends/` | SceneSmith, replay, base adapter |
| `metrics/` | STL specs via RTAMT |
| `verdict/` | Statistics and decision rules |
| `ledger/` | Hashing and signed reports |
| `examples/` | Home-robot demo with SceneSmith |
| `docs/` | Documentation |

## Status
Early development. Built by DeepThought Infinity (DTI.ai).

## License
Apache-2.0
