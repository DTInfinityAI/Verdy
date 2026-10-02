"""Wrap plain functions as a backend: custom simulators and hardware-in-the-loop rigs."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from verdy.backends.base import Backend


class FunctionBackend(Backend):
    """Backend built from two functions.

    Args:
        build: ``build(params, scenario) -> env``. ``params`` are keyed by
            ``grounding.sim`` names.
        rollout: ``rollout(env, policy, seed) -> trace``.
        close: optional ``close()`` called once after the last rollout.
    """

    def __init__(
        self,
        build: Callable[[dict[str, Any], dict], Any],
        rollout: Callable[[Any, Any, int], dict],
        close: Callable[[], None] | None = None,
        name: str = "function",
    ) -> None:
        self._build, self._rollout, self._close = build, rollout, close
        self.name = name

    def build(self, scenario: dict) -> object:
        return self._build(self.sim_params(scenario), scenario)

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        return self._rollout(env, policy, seed)

    def close(self) -> None:
        if self._close is not None:
            self._close()
