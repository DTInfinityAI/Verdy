# Command-line reference

```console
$ verdy --help
```

| Command | Purpose |
| --- | --- |
| `verdy validate FILE...` | Validate ODD and STL spec files (detected by content). `--strict` rejects unapproved LLM-authored parameters. |
| `verdy sample ODD [-n 10] [--sampler stratified] [--seed 0] [--json]` | Print scenarios sampled from an ODD. |
| `verdy run CONFIG` | Run an evaluation and write the evidence report. |
| `verdy verify REPORT [--key-env VAR \| --public-key PEM]` | Check a report's digest, and its signature if a key is given. |
| `verdy plan --max-failure-prob P [--confidence C]` | Number of failure-free runs needed to show failure probability ≤ P. |
| `verdy author "DESCRIPTION" [-o odd.draft.yaml]` | Draft an ODD with Claude (`pip install "verdy[llm]"`, uses the `ANTHROPIC_API_KEY` secret). |
| `verdy improve LOOP_CONFIG [--cycles N] [--sign hmac\|ed25519]` | Run the closed improvement loop: diagnose, collect feedback, train, re-certify on held-out scenarios. Exits with the final certified verdict. See [improvement loop](improvement-loop.md). |
| `verdy secrets status [NAME...] [--fingerprint sha256\|hmac\|none]` | Which secrets are set and where from, with fingerprints. Never prints values. |
| `verdy secrets scan PATH...` | Find credential-shaped strings in files; exits 1 if any are found. |
| `verdy schema {odd,stl_specs}` | Print a bundled JSON Schema. |

## `verdy run`

| Option | Description |
| --- | --- |
| `--runs N` | Override the number of runs. |
| `--seed S` | Override the seed. |
| `--max-failure-prob P` | Override `verdict.max_failure_prob`. |
| `-o, --output PATH` | Report path (default: the config's `output`). |
| `--traces` | Save traces of failed runs next to the report. |
| `--sign {hmac,ed25519}` | Sign the report. HMAC reads the secret named by `--key-env` (default `VERDY_SIGNING_KEY`) from the environment or the secrets file; Ed25519 needs `--key PEM`. |
| `-q, --quiet` | No progress output. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `PASS` (or command succeeded). For `verdy improve`: the final certified verdict. |
| 1 | `FAIL` (or report did not verify) |
| 2 | Invalid input: bad file, config or arguments |
| 3 | `INCONCLUSIVE` |

These make `verdy run` usable as a CI gate:

```yaml
- run: verdy run tests/safety/run.yaml --sign hmac
  env:
    VERDY_SIGNING_KEY: ${{ secrets.VERDY_SIGNING_KEY }}
```

## Run config

See the [`verdy.config`](../verdy/config.py) docstring for every field. Relative paths are
resolved against the config file, and its directory is importable, so `policy:make_policy`
loads `policy.py` next to the config.

```yaml
name: home_robot_tuned
odd: odd.yaml
specs: specs.yaml
policy:
  factory: policy:make_policy
  args: {cruise: 0.8, slow_radius: 2.5}
backend:
  type: sim2d
  options: {dt: 0.1, horizon: 20.0}
sampler: stratified                  # or {type: importance, options: {...}}
runs: 1000
batch_size: 100                      # adaptive samplers update after each batch
seed: 7
verdict: {max_failure_prob: 0.05, confidence: 0.95, min_coverage: 0.9}
keep_traces: failures                # false | true | failures
output: reports/home_robot_tuned.report.json
credentials:
  fingerprint: sha256                # sha256 | hmac | none
```

Configs name secrets but never contain them: a config with a credential-shaped value is
rejected. See [credentials](secrets.md).
