# Getting started

## Install

Verdy needs Python 3.10, 3.11 or 3.12. (RTAMT, used for STL scoring, depends on an ANTLR runtime that does not import on Python 3.13 yet.)

```bash
git clone https://github.com/DTInfinityAI/Verdy.git
cd Verdy
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .                   # add [llm], [sign] or [dev] for the extras
```

| Extra | Adds |
| --- | --- |
| `llm` | `verdy author`: draft ODDs with Claude (`anthropic`) |
| `sign` | Ed25519 report signatures (`cryptography`) |
| `dev` | `pytest` and `ruff` |

## Run the example

```bash
cd examples/home_robot
verdy validate odd.yaml specs.yaml
verdy run run.yaml          # baseline tuning
verdy run run_tuned.yaml    # conservative tuning
```

The baseline navigator gets a `FAIL`: it violates `slow_near_person` in about 10% of
scenarios. The tuned one gets a `PASS`:

```text
Verdict: PASS
  - failure probability is at most 0.03819 (<= 0.05) with 95% confidence
Runs: 1000 (28 failed, 0 errors)
Failure probability: 0.028 [0.01997, 0.03819] at 95% (clopper-pearson)
ODD coverage: 95%, pairwise 89%
Per spec:
  no_collision                 8 violations  p=0.008 (upper 0.01439)
  slow_near_person            25 violations  p=0.025 (upper 0.03474)
  reach_goal                   2 violations  p=0.002 (upper 0.006282)
```

Each run takes about five seconds and writes an evidence report under `reports/`. Check
one with `verdy verify reports/home_robot_tuned.report.json`.

## Evaluate your own policy

1. **Describe the operating domain** in an ODD file ([spec](odd-spec.md)). Start from
   `examples/home_robot/odd.yaml`, or draft one with Claude and review it:

   ```bash
   pip install -e ".[llm]"
   verdy author "Warehouse AMR, 0.5-2 m/s, forklift traffic, dim aisles at night" -o odd.yaml
   ```

2. **Write the safety requirements** as STL specs ([guide](stl-specs.md)).
3. **Connect a backend** that can run your policy and record the signals your specs use:
   your simulator, recorded logs or a HIL rig ([guide](backends.md)).
4. **Wrap the policy** as `act(observation) -> action`.
5. **Write a run config** and pick the target failure probability and confidence
   ([verdicts](verdicts.md)). `verdy plan --max-failure-prob 0.01` tells you roughly how
   many runs that takes.
6. **Run it**: `verdy run run.yaml --sign hmac`.

## Use it from Python

```python
from verdy import VerdictConfig, evaluate, load_odd, load_specs
from verdy.backends import HomeNavSim
from verdy.sampler import StratifiedSampler

odd = load_odd("odd.yaml")
specs = load_specs("specs.yaml")
result = evaluate(
    odd, specs, HomeNavSim(), my_policy, StratifiedSampler(odd, seed=7), n_runs=500,
    verdict=VerdictConfig(max_failure_prob=0.05, confidence=0.95),
)
print(result.summary())
```

`result.report` is the evidence report; `result.runs` holds every run with its scenario,
robustness values and failure status.
