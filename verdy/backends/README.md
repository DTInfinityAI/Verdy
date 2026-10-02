# backends

Execution backends behind a common adapter. Guide: [`docs/backends.md`](../../docs/backends.md).

| Module | Contents |
| --- | --- |
| `base.py` | `Backend` interface and `validate_trace` |
| `sim2d/` | Built-in 2D home-robot simulator |
| `replay/` | Score recorded logs |
| `scenesmith/` | Bridge to a SceneSmith simulation via a small client protocol |
| `function.py` | Wrap two functions as a backend (custom simulators, HIL rigs) |
