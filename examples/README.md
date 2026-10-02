# Examples

| Example | Shows |
| --- | --- |
| [`home_robot/`](home_robot/) | A full evaluation on the built-in simulator: ODD, specs, a policy, and three run configs that reach `FAIL`, `PASS` and `INCONCLUSIVE` |
| [`log_replay/`](log_replay/) | Scoring recorded runs instead of simulating new ones |

Run them from their own directory, for example `cd home_robot && verdy run run.yaml`.
Reports are written to `reports/` there (ignored by git).
