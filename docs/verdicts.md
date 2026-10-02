# Verdicts and statistics

Verdy answers one question: *is the probability that a run fails low enough, and how sure
are we?* "Low enough" is `max_failure_prob`; "how sure" is `confidence`.

```yaml
verdict:
  max_failure_prob: 0.05       # at most 1 in 20 runs may fail
  confidence: 0.95             # one-sided confidence of the bounds
  min_coverage: 0.9            # share of the ODD that must be covered to PASS
  min_runs: 1                  # runs required before deciding anything
  fail_on: [critical, major]   # spec severities that make a run fail
  errors_as_failures: true     # crashed runs and bad traces count as failures
```

## Decision rule

From the runs, Verdy computes a failure-probability estimate with a one-sided lower bound
`L` and upper bound `U` at `confidence`:

| Verdict | Condition | Meaning |
| --- | --- | --- |
| `FAIL` | `L > max_failure_prob` | The policy fails more often than allowed, with the stated confidence. |
| `PASS` | `U ≤ max_failure_prob` and coverage ≥ `min_coverage` | The policy fails at most `max_failure_prob` of the time, with the stated confidence, and the tests covered enough of the ODD. |
| `INCONCLUSIVE` | anything else | Not enough evidence yet: run more scenarios or cover more of the ODD. |

`PASS` and `FAIL` each have an error rate of at most `1 − confidence` when the runs are
representative of the ODD's nominal distribution. One failed run does not mean `FAIL` on
its own: the target is a *rate*, and the verdict says whether the rate is above or below
it.

## How many runs?

With zero failures, the upper bound falls below `p` after `ln(1 − confidence) / ln(1 − p)`
runs (the "rule of three" gives ≈ `3 / p` at 95%):

```console
$ verdy plan --max-failure-prob 0.01
299 failure-free runs are needed to show failure probability <= 0.01 with 95% confidence.
```

Any failures push that number up. Rare-event targets are where
[importance sampling](sampling.md#importance-sampling-cross-entropy) helps.

## Estimators

| Situation | Method | Bounds |
| --- | --- | --- |
| Unweighted runs (Monte Carlo, stratified, replay) | `clopper-pearson` | Exact binomial |
| Weighted runs (importance sampling) | `importance` | Approximate: wider of a normal interval and Clopper-Pearson at the effective sample size |

Stratified sampling is treated as independent sampling, which is conservative: Latin
hypercube designs usually reduce variance.

## Which specs count

Each run is scored against every spec. A run **fails** when any spec whose `severity` is in
`fail_on` is violated (default: `critical` and `major`). Violations of other specs are
still recorded and reported per spec, but do not affect the verdict. Every spec also gets
its own estimate and bounds in the report, which shows what is failing.

## Coverage

Numeric parameters are split into equal-width bins (5 by default); categorical and boolean
parameters have one cell per value. Coverage is the share of cells hit by at least one
scenario, averaged over parameters; pairwise coverage is the share of all two-parameter
cell combinations hit. The report lists the uncovered cells for each parameter.

A rarely occurring value can stay uncovered even with many runs, because sampling follows
how often it happens in deployment. That is the case for `sensor_dropout` above 0.18 in the
home-robot example. Lower `min_coverage`, or add targeted runs, if those cells matter.

## What a verdict does not say

* It covers the ODD and specs it was given. Conditions outside the ODD, and requirements
  not written as specs, are untested.
* Results from a simulator are evidence about the simulator. How far they transfer to the
  real robot is a separate question; replaying real logs and HIL runs help answer it.
* Bounds assume runs are independent and drawn according to the ODD's distributions. If
  the nominal distributions are wrong, so is the failure-rate estimate.
