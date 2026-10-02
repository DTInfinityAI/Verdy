# Verdy

**A driving test for robot AI.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Verdy tells you whether a robot policy is safe to deploy, and how confident you can be.

Describe your operating conditions, and Verdy:

1. **Samples scenarios** across that operating domain
2. **Runs your policy** in simulation or on recorded logs
3. **Scores every run** against formal safety specifications
4. **Returns a verdict** of `PASS`, `FAIL`, or `INCONCLUSIVE`, with confidence bounds and a reproducible evidence trail

---

## How it works

```
 ODD spec ──▶ Scenario sampler ──▶ Execution backend ──▶ STL scoring ──▶ Statistical verdict
 (domain)     (coverage / replay /   (sim / logs / HIL)   (robustness     PASS / FAIL /
               importance sampling)                        margins)       INCONCLUSIVE
                                                                          + confidence bounds
                                                                          + evidence trail
```

---

## Key features

| Feature | Description |
| --- | --- |
| **ODD specs** | Describe operating domains in a typed, versioned schema, with LLM-assisted authoring. |
| **Scenario sampling** | Coverage-driven sampling, log replay, and failure-seeking importance sampling. |
| **Backend-agnostic** | Works with SceneSmith, custom simulators, log replay, and hardware-in-the-loop (HIL). |
| **STL scoring** | Signal Temporal Logic scoring that reports robustness margins, not just pass/fail. |
| **Statistical verdicts** | Failure-probability bounds and coverage reports. |
| **Runtime monitors** | The same specs, deployed on the robot. |

---

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | The policy met the safety specs across the sampled domain within the stated confidence bounds. |
| `FAIL` | One or more runs violated a safety spec. |
| `INCONCLUSIVE` | Not enough evidence yet to reach a confident `PASS` or `FAIL`. |

Every verdict comes with confidence bounds and a reproducible evidence trail, so results can be audited and re-run.

---

## License

Verdy is licensed under the [Apache License 2.0](LICENSE).
