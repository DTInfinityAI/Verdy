# Home robot: crossing a kitchen

A mobile robot drives across a kitchen to a goal while a person walks across its path. The
test question: **is this navigation policy safe enough to deploy?**

| File | Contents |
| --- | --- |
| `odd.yaml` | The operating domain: lighting, floor type, walking speed and timing, route length, speed limit, sensor range and frame dropouts (8 parameters, 1 constraint) |
| `specs.yaml` | Safety specs: `no_collision` (critical), `slow_near_person` (major), `reach_goal` (minor) |
| `policy.py` | The policy under test: a reactive navigator that slows down near a detected person |
| `run.yaml` | Baseline tuning, 1000 stratified scenarios, target ≤ 5% failures |
| `run_tuned.yaml` | Same tests, conservative tuning |
| `run_collisions.yaml` | Collisions only, target ≤ 1%, with importance sampling |
| `run_v1_2.yaml` | Release 1.2.0, a "faster" retune that regresses: for `verdy history` |
| `improve.yaml` | Improvement loop: take the baseline from `FAIL` to a certified `PASS` |
| `simulated_operator.py` | Stand-in operator: expert driving style and pairwise preferences |
| `demos/operator.jsonl`, `make_demos.py` | Recorded "operator" sessions, and the script that records them |

Scenarios run on Verdy's built-in 2D simulator (`sim2d`), so no external simulator is
needed. Results describe that simulator, not a real robot.

## Run

```bash
verdy validate odd.yaml specs.yaml
verdy run run.yaml
verdy run run_tuned.yaml
verdy run run_collisions.yaml
```

## Results

| Config | Verdict | Failures | Failure probability (95% bounds) |
| --- | --- | --- | --- |
| `run.yaml` | `FAIL` (exit 1) | 101 / 1000 | 0.101 [0.086, 0.118] |
| `run_tuned.yaml` | `PASS` (exit 0) | 28 / 1000 | 0.028 [0.020, 0.038] |
| `run_collisions.yaml` | `INCONCLUSIVE` (exit 3) | 2 / 1000 | 0.0033 [0, 0.011] (weighted) |

* **Baseline → FAIL.** The navigator collides in 2% of scenarios and is still moving
  faster than 0.3 m/s within 0.5 m of the person in 10%. With 95% confidence, more than 5%
  of runs fail.
* **Tuned → PASS.** Cruising at 0.8 m/s and braking earlier (`slow_radius` 2.5 m,
  `stop_radius` 0.9 m) brings failures to 2.8%, and the upper bound (3.8%) is below the
  5% target.
* **Collisions at ≤ 1% → INCONCLUSIVE.** The tuned navigator rarely collides, but 1000
  runs cannot yet show the collision rate is below 1%. More runs are needed.

Failed runs' traces are saved to `reports/<name>_traces/` for debugging. Change the
tuning in `run.yaml`, or edit `policy.py`, and run again to compare.

## Track releases

```bash
pip install -e "../..[store]"
verdy run run.yaml --store && verdy run run_tuned.yaml --store && verdy run run_v1_2.yaml --store
verdy history home-navigator --by-spec
```

The configs declare `policy_name: home-navigator` and versions 1.0.0, 1.1.0 and 1.2.0.
History shows 1.0.0 `FAIL` → 1.1.0 `PASS` → 1.2.0 `INCONCLUSIVE`, and flags 1.2.0 as a
regression. See [docs/evidence-store.md](../../docs/evidence-store.md).

## Improve it automatically

```bash
verdy improve improve.yaml       # about 1.5 minutes
```

| Stage | Held-out failure rate | Verdict |
| --- | --- | --- |
| Starting policy (`run.yaml` tuning) | 11.4% | `FAIL` |
| After pretraining on the operator's sessions | 6.2% | `INCONCLUSIVE` |
| Cycle 1 (promoted) | 4.3% | `INCONCLUSIVE` |
| Cycle 2 (rejected: reached the goal less often) | 2.9% | `PASS` (not promoted) |
| Cycle 3 (promoted) | 1.1% | `PASS` |

Each cycle diagnoses the current policy, asks the (simulated) operator to compare 30
pairs of runs, practices on 120 scenarios concentrated where it failed, and is then
certified, with the incumbent, on 1000 fresh scenarios it never trained on. The final
policy has 0.3% collisions and still misses the goal in only 0.7% of runs: the
`reach_goal` guard rejected cycle 2's candidate, which had bought safety by stopping for
people from 1.3 m away and so reached the goal less often. Results are in `reports/improve/`. See
[docs/improvement-loop.md](../../docs/improvement-loop.md).
