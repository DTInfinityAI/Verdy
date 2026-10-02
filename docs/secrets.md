# Credentials and API keys

Verdy talks to paid APIs: Claude (scene prompts and ODD drafting) and, through SceneSmith,
OpenAI. It handles their keys so they are used but never exposed.

| Rule | How |
| --- | --- |
| Keys come from a safe place | Environment variables, or a private secrets file. Never from run configs or ODD files. |
| Configs cannot hold keys | A config containing a credential-shaped value is rejected before anything runs. Configs refer to secrets by name only. |
| Keys are never printed | A loaded key is a `Secret` object that prints as its name, source and fingerprint. The value is only handed to the client that needs it. |
| Reports store fingerprints, not keys | Each report lists the secrets a run used, with a one-way hash. |
| Output is redacted | Error messages, logs, SceneSmith output and reports pass through a redactor that removes every loaded key and anything shaped like an API key. |
| Least privilege | SceneSmith subprocesses get a minimal environment with only the keys they need. They never see your Anthropic key. |

## Setting keys

| Secret | Used for |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude: `verdy author` and the SceneSmith `claude` prompt writer |
| `OPENAI_API_KEY` | SceneSmith's scene-generation and validation agents |
| `GOOGLE_API_KEY` | Optional: SceneSmith's Gemini image backend |
| `VERDY_SIGNING_KEY` | HMAC report signatures (`verdy run --sign hmac`) |
| `VERDY_FINGERPRINT_KEY` | Optional: keyed (`hmac`) fingerprints in reports |

**Environment variables** (CI, cloud sessions, containers): set them in your platform's
secret store, e.g. GitHub Actions secrets or your cloud environment's settings, and they
reach Verdy as environment variables.

**Secrets file** (your own machine): put `KEY=value` lines in
`~/.config/verdy/secrets.env`, or point `VERDY_SECRETS_FILE` somewhere else. Keep it out
of every repository, and make it private; Verdy refuses to read it otherwise:

```bash
mkdir -p ~/.config/verdy
$EDITOR ~/.config/verdy/secrets.env   # ANTHROPIC_API_KEY=...  OPENAI_API_KEY=...
chmod 600 ~/.config/verdy/secrets.env
```

Environment variables take precedence over the file. If `ANTHROPIC_API_KEY` is set in
neither, Claude calls fall back to the Anthropic SDK's own credential lookup (for example
an `ant auth login` profile).

Check what is set without revealing anything:

```console
$ verdy secrets status
Secrets file: /home/me/.config/verdy/secrets.env
  ANTHROPIC_API_KEY        set (file:secrets.env)  sha256:3f9a0c1d2e4b5a69
  GOOGLE_API_KEY           not set
  OPENAI_API_KEY           set (env)  sha256:81c0e7d4a2b39f50
  VERDY_FINGERPRINT_KEY    not set
  VERDY_SIGNING_KEY        not set
```

## Fingerprints in reports

Every evidence report has an `inputs.credentials` entry recording which keys the run used:

```json
"credentials": {
  "fingerprint": "hmac",
  "secrets": {
    "ANTHROPIC_API_KEY": {"set": true, "source": "env", "fingerprint": "hmac-sha256:5d1e..."},
    "OPENAI_API_KEY": {"set": true, "source": "env", "fingerprint": "hmac-sha256:a07c..."},
    "GOOGLE_API_KEY": {"set": false}
  }
}
```

Choose the hashing in the run config:

```yaml
credentials:
  fingerprint: hmac     # sha256 (default) | hmac | none
```

| Mode | Stored | Use when |
| --- | --- | --- |
| `sha256` | First 16 hex digits of SHA-256 of the key | Default. API keys are long and random, so the hash cannot be reversed. |
| `hmac` | HMAC-SHA256 keyed with `VERDY_FINGERPRINT_KEY` | Reports are shared outside your team: without the fingerprint key, nobody can even check a guessed key against the report. |
| `none` | Only whether each secret was set, and where from | You do not want any key-derived value recorded. |

Fingerprints let an auditor confirm which key (for example, which account) produced a
report, and that two reports used the same one, without ever seeing a key.

## Checking for leaks

```console
$ verdy secrets scan .
No credentials found.
```

`verdy secrets scan PATH...` reports `file:line` (never the value) for every
credential-shaped string, and exits with 1 if it finds any. CI runs it on the whole
repository. If it ever finds a real key, remove it and **rotate the key**: anything that
was committed should be treated as leaked.

## In Python

```python
from verdy.secrets import get_secret, subprocess_env

key = get_secret("OPENAI_API_KEY")        # Secret; raises SecretError if not set
print(key)                                # Secret(name='OPENAI_API_KEY', source='env', sha256:...)
client = SomeClient(api_key=key.reveal()) # reveal only where the value is needed

env = subprocess_env(required=["OPENAI_API_KEY"], extra={"WANDB_MODE": "disabled"})
```

`verdy.secrets.redact(text)` and `RedactingFilter` (for `logging`) remove loaded keys and
credential-shaped strings from text you show or store.
