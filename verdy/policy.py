"""Helpers for the policies under test.

A policy is either a callable ``policy(observation) -> action`` or an object with an
``act(observation)`` method. It may also define ``reset(seed)``, called before each
rollout. What an observation and an action are is decided by the backend.
"""
from __future__ import annotations

import inspect
from typing import Any


def act(policy: Any, observation: Any) -> Any:
    if hasattr(policy, "act"):
        return policy.act(observation)
    if callable(policy):
        return policy(observation)
    raise TypeError(f"policy {policy!r} is neither callable nor has an act() method")


def reset(policy: Any, seed: int) -> None:
    if hasattr(policy, "reset"):
        policy.reset(seed)


def describe(policy: Any) -> str:
    """A stable reference to the policy, recorded in the evidence ledger."""
    target = policy if inspect.isclass(policy) or inspect.isroutine(policy) else type(policy)
    module = getattr(target, "__module__", "?")
    name = getattr(target, "__qualname__", getattr(target, "__name__", repr(target)))
    return f"{module}:{name}"
