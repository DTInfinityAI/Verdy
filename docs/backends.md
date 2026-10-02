# Execution backends

A backend turns a scenario into a rollout. Every backend implements
`verdy.backends.Backend`:

| Method | Description |
| --- | --- |
| `build(scenario) -> env` | Realize a scenario (`{"id", "params", "seed", "metadata"}`) as a runnable environment. |
| `rollout(env, policy, seed) -> trace` | Run the policy and return a [trace](stl-specs.md#traces). |
| `bind(odd)` | Called once with the ODD before any rollout. Optional. |
| `close()` | Called once after the last rollout. Optional. |

`self.sim_params(scenario)` returns the scenario parameters keyed by each parameter's
`grounding.sim` name, so ODD names and simulator names can differ.

| Backend | Config `type` | Purpose |
| --- | --- | --- |
| `HomeNavSim` | `sim2d` | Built-in 2D home-robot simulator, for demos and tests |
| `ReplayBackend` | `replay` | Score recorded logs |
| `SceneSmithBackend` | `module:factory` | Bridge to a SceneSmith simulation |
| `FunctionBackend` | `module:factory` | Wrap your own simulator or HIL rig in two functions |

In a run config, a non-built-in backend is given as `module:attribute`, which is called
with `options` and must return a `Backend`.

## Policies

A policy is a callable `policy(observation) -> action`, or an object with
`act(observation)` and optionally `reset(seed)`. The backend decides what observations
and actions are. Run configs reference a policy as `module:attribute` (a class is
instantiated) or as `{factory: module:attribute, args: {...}}`.

## Built-in simulator (`sim2d`)

A robot drives from the origin to a goal while a person crosses its path. Dim lighting
adds detection noise and missed detections, dropouts hide the person, and low friction
limits braking. The person steps around the robot when it is standing still, but not when
it is moving: avoiding the person is the robot's job. It is a teaching and testing tool,
not evidence about a real robot.

Scenario keys (all optional): `lighting_lux`, `floor_friction` or `floor_type`
(`tile`, `wood`, `carpet`, `rug`, `wet_tile`), `person_speed`, `person_delay`,
`goal_distance`, `max_speed`, `sensor_range`, `sensor_dropout`.

Signals: `speed`, `dist_obstacle` (gap between robot and person bodies), `dist_goal`,
`detected`. Observations: `t`, `position`, `velocity`, `goal`, `obstacle` (noisy offset
to the person, or `None`), `max_speed`. Actions: desired velocity `(vx, vy)`.

## Log replay

Each log is a JSON file:

```json
{"params": {"lighting": 120, "floor_type": "tile"},
 "trace": {"time": [0.0, 0.1, ...], "speed": [...], "dist_obstacle": [...]}}
```

Use `sampler: {type: replay, options: {logs: path/to/logs}}` with `backend: replay`. The
recorded trace is scored as is; the policy is not re-run.

## SceneSmith

Verdy does not depend on SceneSmith. `SceneSmithBackend` drives any client that implements
this protocol, so connecting SceneSmith means writing a thin client around your
installation:

```python
class MySceneSmithClient:
    def load(self, scene: dict, seed: int): ...        # build the scene, return a handle
    def observe(self, handle): ...                     # observation for the policy
    def step(self, handle, action) -> None: ...        # advance one control step
    def signals(self, handle) -> dict[str, float]: ... # current values of STL signals
    def done(self, handle) -> bool: ...                # episode finished early?
    def close(self, handle) -> None: ...


def make_backend(dt: float = 0.05, horizon: float = 30.0):
    return SceneSmithBackend(MySceneSmithClient(), dt=dt, horizon=horizon)
```

```yaml
backend:
  type: my_scenesmith:make_backend
  options: {dt: 0.05, horizon: 30.0}
```

The scene dict holds scenario parameters keyed by `grounding.sim`. If an episode ends
early, the final signal values are held until `horizon`, so all traces have equal length.

## Custom simulators and hardware in the loop

```python
from verdy.backends import FunctionBackend

def build(params, scenario):
    return my_sim.make_world(**params)

def rollout(world, policy, seed):
    ...  # step policy and world, collect signals
    return {"time": times, "speed": speeds, "dist_obstacle": gaps}

backend = FunctionBackend(build, rollout, close=my_sim.shutdown, name="my-sim")
```

For a hardware-in-the-loop rig, `build` configures the rig for the scenario and `rollout`
runs the physical trial and returns the logged signals. Exceptions raised by a backend are
recorded as errored runs (counted as failures by default) instead of stopping the
evaluation.
