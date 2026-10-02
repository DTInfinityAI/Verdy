import json

import numpy as np
import pytest

from verdy.backends import (
    FunctionBackend,
    HomeNavSim,
    ReplayBackend,
    SceneClientBackend,
    TraceError,
    validate_trace,
)
from verdy.backends.sim2d import cautious_policy


def test_validate_trace():
    assert validate_trace({"time": [0, 0.1, 0.2], "x": [1, 2, 3]})["x"] == [1.0, 2.0, 3.0]
    for bad in [
        {"x": [1, 2]},
        {"time": [0]},
        {"time": [0, 0.1, 0.1]},
        {"time": [0, 0.1, 0.5]},
        {"time": [0, 0.1], "x": [1]},
    ]:
        with pytest.raises(TraceError):
            validate_trace(bad)
    with pytest.raises(TraceError, match="missing"):
        validate_trace({"time": [0, 1]}, {"speed"})


def scenario(**params):
    return {"id": "s0", "params": params, "seed": 1, "metadata": {}}


def test_sim2d_grounding_and_determinism(odd):
    sim = HomeNavSim(horizon=5.0)
    sim.bind(odd)
    world = sim.build(scenario(lighting=50, floor_type="tile"))
    assert world.config["lighting_lux"] == 50  # mapped through grounding.sim
    assert world.config["floor_friction"] == 0.5
    a = sim.rollout(world, cautious_policy(), seed=3)
    b = sim.rollout(world, cautious_policy(), seed=3)
    assert a == b
    assert len(a["time"]) == 51
    assert set(a) == {"time", "speed", "dist_obstacle", "dist_goal", "detected"}
    with pytest.raises(ValueError):
        sim.build(scenario(floor_type="lava"))


def test_sim2d_policy_reaches_goal_without_person():
    sim = HomeNavSim(horizon=15.0)
    trace = sim.rollout(sim.build(scenario(person_delay=100, goal_distance=4)),
                        cautious_policy(), 0)
    assert trace["dist_goal"][-1] < 0.1
    assert max(trace["speed"]) <= 1.0 + 1e-9


def test_sim2d_reckless_policy_collides():
    sim = HomeNavSim(horizon=15.0)
    world = sim.build(scenario(person_delay=0.5, person_speed=1.0, goal_distance=6,
                               floor_friction=0.3, max_speed=1.5))
    trace = sim.rollout(world, lambda obs: (1.5, 0.0), 0)
    assert min(trace["dist_obstacle"]) < 0


def test_replay_backend(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"params": {}, "trace": {"time": [0, 1], "x": [1, 2]}}))
    rb = ReplayBackend()
    env = rb.build({"id": "s0", "params": {}, "metadata": {"log_path": str(path)}})
    assert rb.rollout(env, None, 0)["x"] == [1, 2]
    with pytest.raises(ValueError):
        rb.build({"id": "s0", "params": {}, "metadata": {}})


class FakeClient:
    def __init__(self):
        self.closed = 0

    def load(self, scene, seed):
        return {"x": 0.0, "scene": scene}

    def observe(self, handle):
        return handle["x"]

    def step(self, handle, action):
        handle["x"] += action

    def signals(self, handle):
        return {"x": handle["x"]}

    def done(self, handle):
        return handle["x"] >= 2

    def close(self, handle):
        self.closed += 1


def test_scene_client_bridge(odd):
    client = FakeClient()
    backend = SceneClientBackend(client, dt=0.5, horizon=3.0)
    backend.bind(odd)
    env = backend.build(scenario(lighting=80))
    assert env["scene"] == {"lighting_lux": 80}
    trace = backend.rollout(env, lambda x: 1.0, 0)
    assert trace["time"] == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    assert trace["x"] == [0, 1, 2, 2, 2, 2, 2]  # held after done
    assert client.closed == 1
    with pytest.raises(TypeError):
        SceneClientBackend(object())


def test_function_backend():
    closed = []
    backend = FunctionBackend(
        build=lambda params, sc: params["k"],
        rollout=lambda env, pol, seed: {"time": [0, 1], "y": list(np.full(2, env))},
        close=lambda: closed.append(True),
    )
    assert backend.rollout(backend.build(scenario(k=3)), None, 0)["y"] == [3, 3]
    backend.close()
    assert closed == [True]
