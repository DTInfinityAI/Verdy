# sampler

Scenario generation: stratified coverage, log replay, importance sampling. Guide:
[`docs/sampling.md`](../../docs/sampling.md).

| Module | Contents |
| --- | --- |
| `base.py` | `Sampler` interface and the `Scenario` record |
| `distributions.py` | Nominal distributions parsed from each parameter's `distribution` |
| `monte_carlo.py` | Independent draws from the nominal distribution |
| `stratified.py` | Latin hypercube sampling for even coverage |
| `importance.py` | Cross-entropy importance sampling with a defensive mixture |
| `replay.py` | One scenario per recorded log |
