"""Step-by-step bridge to a simulator that loads SceneSmith scenes.

Use this when your policy runs closed-loop in a simulator (for example Drake with a
SceneSmith ``.dmd.yaml`` scene) and you want STL specs over signals recorded at every
control step. For SceneSmith's own generate → act → validate pipeline, use
:class:`verdy.backends.scenesmith.SceneSmithBackend` instead.

The adapter drives any client that implements the small :class:`SceneClient` protocol:

.. code-block:: python

    class MySceneSmithClient:
        def load(self, scene: dict, seed: int): ...      # build the scene, return a handle
        def observe(self, handle) -> object: ...         # observation for the policy
        def step(self, handle, action) -> None: ...      # advance one control step
        def signals(self, handle) -> dict[str, float]: ...  # current values of STL signals
        def done(self, handle) -> bool: ...              # episode finished early?
        def close(self, handle) -> None: ...

    backend = SceneClientBackend(MySceneSmithClient(), dt=0.05, horizon=30.0)

Scenario parameters are passed to ``load`` keyed by their ``grounding.sim`` names. If an
episode ends early, the last signal values are held until ``horizon`` so every trace has
the same length.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from verdy import policy as policy_api
from verdy.backends.base import Backend


@runtime_checkable
class SceneClient(Protocol):
    def load(self, scene: dict, seed: int) -> Any: ...
    def observe(self, handle: Any) -> Any: ...
    def step(self, handle: Any, action: Any) -> None: ...
    def signals(self, handle: Any) -> dict[str, float]: ...
    def done(self, handle: Any) -> bool: ...
    def close(self, handle: Any) -> None: ...


class SceneClientBackend(Backend):
    name = "scene-client"

    def __init__(self, client: SceneClient, dt: float = 0.05, horizon: float = 30.0) -> None:
        if not isinstance(client, SceneClient):
            raise TypeError("client must implement load/observe/step/signals/done/close")
        self.client = client
        self.dt = dt
        self.horizon = horizon

    def build(self, scenario: dict) -> dict:
        return {"scene": self.sim_params(scenario), "id": scenario.get("id")}

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        assert isinstance(env, dict)
        handle = self.client.load(env["scene"], seed)
        policy_api.reset(policy, seed)
        steps = int(round(self.horizon / self.dt)) + 1
        trace: dict[str, list[float]] = {"time": []}
        last: dict[str, float] = {}
        finished = held = False
        try:
            for k in range(steps):
                if not held:
                    last = {key: float(v) for key, v in self.client.signals(handle).items()}
                    held = finished  # record the final state once, then hold it
                trace["time"].append(round(k * self.dt, 9))
                for key, value in last.items():
                    trace.setdefault(key, []).append(value)
                if not finished:
                    action = policy_api.act(policy, self.client.observe(handle))
                    self.client.step(handle, action)
                    finished = bool(self.client.done(handle))
        finally:
            self.client.close(handle)
        return trace


__all__ = ["SceneClient", "SceneClientBackend"]
