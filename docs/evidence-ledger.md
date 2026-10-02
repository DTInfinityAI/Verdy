# Evidence ledger

Every `verdy run` writes a JSON report with everything needed to audit and reproduce the
verdict.

| Section | Contents |
| --- | --- |
| `report_version`, `verdy_version`, `created_at` | Format and tool versions, UTC timestamp |
| `environment` | Python version, platform, versions of `verdy`, `numpy`, `scipy`, `rtamt`, `jsonschema` |
| `inputs` | Full ODD and specs with SHA-256 digests, policy reference, backend and its settings, sampler settings and seed, verdict config, run config, SHA-256 of the config, ODD and specs files, and fingerprints of the credentials used |
| `runs` | Per run: scenario id, parameters, seed, weight, robustness per spec, violated specs, failed flag, error, trace SHA-256, duration, and backend details (e.g. the SceneSmith prompt and validator result) |
| `results` | Verdict and reasons, failure-probability estimate, per-spec estimates, coverage, run and error counts |
| `integrity` | `body_sha256` over everything above, and an optional `signature` |

Every string in a report is passed through the secrets redactor before the digest is
computed, so an API key that appears in an error message or a config can never be written
to a report. Credentials appear only as fingerprints; see
[credentials](secrets.md#fingerprints-in-reports).

## Integrity

`body_sha256` is the SHA-256 of the canonical JSON form of the report body: sorted keys,
no whitespace, non-finite numbers as strings. Changing any field (a verdict, a single
robustness value) changes the digest.

```console
$ verdy verify reports/home_robot_tuned.report.json
reports/home_robot_tuned.report.json: digest OK, signature not checked; verdict PASS
```

## Signing

A digest proves a report was not changed by accident; a signature shows who produced it.

**HMAC-SHA256** (built in): a shared secret signs and verifies. It is read like any other
[secret](secrets.md): environment variable or secrets file.

```console
$ verdy run run.yaml --sign hmac
$ verdy verify reports/x.report.json --key-env VERDY_SIGNING_KEY
```

**Ed25519** (`pip install "verdy[sign]"`): a private key signs, anyone with the public
key verifies.

```console
$ openssl genpkey -algorithm ed25519 -out verdy.key
$ openssl pkey -in verdy.key -pubout -out verdy.pub
$ verdy run run.yaml --sign ed25519 --key verdy.key
$ verdy verify reports/x.report.json --public-key verdy.pub
```

In Python: `verdy.ledger.sign_report(report, method, key)` and
`verdy.ledger.verify_report(report, key)`.

## Reproducing a run

Samplers and the built-in simulator are deterministic given their seeds, so re-running
the same config with the same seed and software versions reproduces every scenario, trace
and robustness value. To check a single run, take its `params` and `seed` from the report
and roll it out again; its trace should hash to the recorded `trace_sha256`. Durations and
`created_at` naturally differ between runs.

`verdy run --traces` (or `keep_traces: failures` in the config) also saves the full trace
of every failed run next to the report, for debugging. `verdy run --store` keeps every
run's trace as Parquet at its `trace_sha256` and indexes the report; see
[evidence store](evidence-store.md).
