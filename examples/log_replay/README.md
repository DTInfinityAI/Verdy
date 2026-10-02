# Log replay

Score recorded runs instead of simulating new ones. Each file in `logs/` holds the
conditions a run was recorded under (`params`) and its signals (`trace`). The replay
backend scores each trace against the home-robot specs without re-running a policy.

```bash
verdy run run.yaml
```

```text
Verdict: INCONCLUSIVE
  - upper bound 0.2923 is above 0.05 and lower bound 0.03495 is below it; more runs are needed
Runs: 24 (3 failed, 0 errors)
Failure probability: 0.125 [0.03495, 0.2923] at 95% (clopper-pearson)
ODD coverage: 88%, pairwise 46%
```

24 logs are not enough evidence either way, and the coverage report shows which parts of
the ODD the field data has not reached.

The sample logs were generated from the built-in simulator with `python make_logs.py`.
Real logs would be exported from robot recordings (e.g. ROS bags) into the same JSON
format:

```json
{"params": {"lighting": 120, "floor_type": "tile", "...": "..."},
 "trace": {"time": [0.0, 0.1, "..."], "speed": ["..."], "dist_obstacle": ["..."],
           "dist_goal": ["..."]}}
```
