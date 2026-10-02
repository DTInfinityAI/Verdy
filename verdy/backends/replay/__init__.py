"""Log replay backend: score recorded runs without a simulator.

Each log is a JSON file::

    {
      "params": {"lighting_lux": 120, "floor_type": "tile"},
      "trace": {"time": [0.0, 0.1, ...], "speed": [...], "dist_obstacle": [...]}
    }

Pair it with :class:`verdy.sampler.LogReplaySampler`, which yields one scenario per log.
The policy is not re-run: the recorded trace is scored as is (open-loop replay).
"""
from __future__ import annotations

import json
from pathlib import Path

from verdy.backends.base import Backend


class ReplayBackend(Backend):
    name = "replay"

    def build(self, scenario: dict) -> dict:
        path = scenario.get("metadata", {}).get("log_path")
        if not path:
            raise ValueError(
                f"scenario {scenario.get('id')} has no metadata.log_path; "
                "use the replay sampler with the replay backend"
            )
        data = json.loads(Path(path).read_text("utf-8"))
        if "trace" not in data:
            raise ValueError(f"{path}: log has no 'trace'")
        return data["trace"]

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        assert isinstance(env, dict)
        return env


__all__ = ["ReplayBackend"]
