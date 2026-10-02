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
