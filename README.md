
# Verdy
A driving test for robot AI.
Verdy tells you whether a robot policy is safe to deploy, and how confident you can be. Describe your operating conditions, and Verdy samples scenarios across that domain, runs your policy in simulation or on recorded logs, scores every run against formal safety specs, and returns a PASS / FAIL / INCONCLUSIVE verdict with confidence bounds and a reproducible evidence trail.
# Key features

# ODD specs
 
describe operating domains in a typed, versioned schema (LLM-assisted authoring)

# Scenario sampling

coverage, log replay, and failure-seeking importance sampling

# Backend-agnostic

SceneSmith, custom simulators, log replay, HIL

# STL scoring

robustness margins, not just pass/fail
# Statistical verdicts

failure-probability bounds and coverage reports

# Runtime monitors

the same specs, deployed on the robot