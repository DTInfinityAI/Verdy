"""The policy under test: a reactive navigation controller for the home robot.

Swap this file for a wrapper around your own policy (a neural network, a planner stack,
...). Verdy only needs ``act(observation) -> action`` and, optionally, ``reset(seed)``.
"""
from __future__ import annotations

import math

BODY_GAP = 0.5  # robot radius + person radius, in metres


class CautiousNavigator:
    """Drive to the goal; slow down near a detected person and stop when close.

    The last detection is remembered for ``memory`` seconds, so a single dropped
    perception frame does not make the robot forget the person.
    """

    def __init__(
        self,
        cruise: float = 1.0,
        slow_radius: float = 1.5,
        stop_radius: float = 0.6,
        memory: float = 0.3,
    ) -> None:
        self.cruise = cruise
        self.slow_radius = slow_radius
        self.stop_radius = stop_radius
        self.memory = memory
        self._last_seen: tuple[float, tuple[float, float]] | None = None

    def reset(self, seed: int) -> None:
        self._last_seen = None

    def act(self, obs: dict) -> tuple[float, float]:
        x, y = obs["position"]
        gx, gy = obs["goal"]
        dx, dy = gx - x, gy - y
        dist = math.hypot(dx, dy)
        if dist < 0.05:
            return (0.0, 0.0)

        if obs["obstacle"] is not None:
            self._last_seen = (obs["t"], obs["obstacle"])
        obstacle = None
        if self._last_seen and obs["t"] - self._last_seen[0] <= self.memory:
            obstacle = self._last_seen[1]

        speed = min(self.cruise, obs["max_speed"], dist)
        if obstacle is not None:
            gap = math.hypot(*obstacle) - BODY_GAP
            if gap < self.stop_radius:
                speed = 0.0
            elif gap < self.slow_radius:
                speed *= (gap - self.stop_radius) / (self.slow_radius - self.stop_radius)
        return (dx / dist * speed, dy / dist * speed)


def make_policy(**kwargs: float) -> CautiousNavigator:
    return CautiousNavigator(**kwargs)
