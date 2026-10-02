# SceneSmith: pick and place in generated homes

Evaluate a household pick-and-place policy on scenes that
[SceneSmith](https://github.com/nepfaff/scenesmith) generates for each sampled scenario.

| File | Contents |
| --- | --- |
| `odd.yaml` | Room type, clutter, room size, target object, high-shelf placement, lighting |
| `specs.yaml` | `task_completed` (critical), `task_score` (major), `most_requirements_met` (minor) |
| `policy.py` | `DoNothingPolicy` baseline, and how to wrap your robot |
| `run.yaml` | 20 stratified scenarios, Claude-written scene prompts, HMAC key fingerprints |

## Requirements

1. **SceneSmith**, installed as its README describes (it needs a GPU and pins Python
   3.11 with its own dependencies):

   ```bash
   git clone https://github.com/nepfaff/scenesmith ~/scenesmith
   cd ~/scenesmith && uv sync
   ```

2. **Keys**, set as environment variables or in `~/.config/verdy/secrets.env` (`chmod 600`).
   Never put them in `run.yaml`; Verdy rejects configs that contain keys.

   | Secret | Why |
   | --- | --- |
   | `OPENAI_API_KEY` | SceneSmith's generation and validation agents |
   | `ANTHROPIC_API_KEY` | Claude writes the scene prompts (`pip install "verdy[llm]"`) |
   | `VERDY_FINGERPRINT_KEY` | Keyed fingerprints in the report (`credentials.fingerprint: hmac`) |

   ```bash
   verdy secrets status
   ```

## Run

```bash
verdy validate odd.yaml specs.yaml
verdy run run.yaml
```

With `DoNothingPolicy` every task fails, which checks the pipeline end to end. Then
replace `policy:DoNothingPolicy` with your robot policy (see `policy.py`).

Generated scenes are cached in `.verdy/scenesmith/scenes/`, so rerunning the same
scenarios (same seed) reuses them. Each run's final scene, generation log and validation
log are under `.verdy/scenesmith/runs/` and `scenes/`. Logs are redacted before they are
written.

The report records, for every run, the scene prompt, SceneSmith's per-requirement scores
and reasoning, and under `inputs.credentials`, fingerprints of the keys used (never the
keys).

This example has not been run end to end with a real SceneSmith installation. The
integration is tested against a stand-in SceneSmith checkout that follows the same
interfaces.
