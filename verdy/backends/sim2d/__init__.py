"""A lightweight 2D home-robot simulator.

A mobile robot drives from the origin to a goal along the x-axis while a person crosses
its path. Perception and braking degrade with the scenario: dim lighting adds range noise
and missed detections, sensor dropouts hide the person, and low floor friction limits
deceleration. It needs no external simulator, so Verdy's pipeline runs end to end
anywhere; use it for demos, tests and policy smoke checks, not as evidence about a real
robot.

Scenario parameters (match by name or by ``grounding.sim``; all optional):

=================  =======  =====================================================
Key                Default  Meaning
=================  =======  =====================================================
lighting_lux       300      Ambient light; lower means noisier, less reliable sensing
floor_friction     0.6      Tyre-floor friction coefficient; limits acceleration
floor_type         -        tile, wood, carpet, rug or wet_tile; sets floor_friction
person_speed       1.0      Walking speed of the crossing person (m/s)
person_delay       2.0      Time before the person starts walking (s)
goal_distance      6.0      Distance from start to goal (m)
max_speed          1.0      Robot's top speed (m/s)
sensor_range       3.0      Maximum detection range (m)
sensor_dropout     0.0      Probability of losing a frame
=================  =======  =====================================================

Observations passed to the policy are dicts with ``t``, ``position``, ``velocity``,
``goal`` (all 2-tuples except ``t``), ``obstacle`` (estimated offset to the person, or
``None`` when not detected) and ``max_speed``. Actions are desired velocity vectors
``(vx, vy)`` in m/s.

Recorded signals: ``speed``, ``dist_obstacle`` (gap between robot and person bodies),
``dist_goal`` and ``detected`` (1 when the person was detected in that frame).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from verdy import policy as policy_api
from verdy.backends.base import Backend

ROBOT_RADIUS = 0.25
PERSON_RADIUS = 0.25
GRAVITY = 9.81

FLOOR_FRICTION = {"tile": 0.5, "wood": 0.6, "carpet": 0.8, "rug": 0.7, "wet_tile": 0.25}

DEFAULTS = {
    "lighting_lux": 300.0,
    "floor_friction": 0.6,
    "person_speed": 1.0,
    "person_delay": 2.0,
    "goal_distance": 6.0,
    "max_speed": 1.0,
    "sensor_range": 3.0,
    "sensor_dropout": 0.0,
}


@dataclass
class HomeNavWorld:
    config: dict[str, float]
    dt: float
    horizon: float


class HomeNavSim(Backend):
    """Built-in 2D home-navigation simulator (see module docstring)."""

    name = "sim2d"

    def __init__(self, dt: float = 0.1, horizon: float = 20.0) -> None:
        self.dt = dt
        self.horizon = horizon

    def build(self, scenario: dict) -> HomeNavWorld:
        params = self.sim_params(scenario)
        config = dict(DEFAULTS)
        if "floor_type" in params:
            if params["floor_type"] not in FLOOR_FRICTION:
                raise ValueError(f"unknown floor_type {params['floor_type']!r}")
            config["floor_friction"] = FLOOR_FRICTION[params["floor_type"]]
        config.update({k: float(v) for k, v in params.items() if k in DEFAULTS})
        return HomeNavWorld(config=config, dt=self.dt, horizon=self.horizon)

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        assert isinstance(env, HomeNavWorld)
        c, dt = env.config, env.dt
        rng = np.random.default_rng(seed)
        policy_api.reset(policy, seed)

        goal = np.array([c["goal_distance"], 0.0])
        robot = np.zeros(2)
        vel = np.zeros(2)
        crossing_x = 0.6 * c["goal_distance"]
        person_start = np.array([crossing_x, -2.5])
        a_max = c["floor_friction"] * GRAVITY * 0.4
        noise_sigma = 0.03 + 15.0 / max(c["lighting_lux"], 1.0)
        p_detect = (1.0 - c["sensor_dropout"]) * min(1.0, 0.5 + c["lighting_lux"] / 300.0)

        steps = int(round(env.horizon / dt)) + 1
        trace: dict[str, list[float]] = {
            "time": [], "speed": [], "dist_obstacle": [], "dist_goal": [], "detected": [],
        }
        person = person_start.copy()
        for k in range(steps):
            t = k * dt

            offset = person - robot
            center_dist = float(np.linalg.norm(offset))
            detected = center_dist <= c["sensor_range"] and rng.random() < p_detect
            obstacle = None
            if detected:
                noisy = offset + rng.normal(0.0, noise_sigma, size=2)
                obstacle = (float(noisy[0]), float(noisy[1]))

            trace["time"].append(round(t, 9))
            trace["speed"].append(float(np.linalg.norm(vel)))
            trace["dist_obstacle"].append(center_dist - ROBOT_RADIUS - PERSON_RADIUS)
            trace["dist_goal"].append(float(np.linalg.norm(goal - robot)))
            trace["detected"].append(1.0 if detected else 0.0)

            obs = {
                "t": t,
                "position": (float(robot[0]), float(robot[1])),
                "velocity": (float(vel[0]), float(vel[1])),
                "goal": (float(goal[0]), float(goal[1])),
                "obstacle": obstacle,
                "max_speed": c["max_speed"],
            }
            command = np.asarray(policy_api.act(policy, obs), dtype=float).reshape(2)
            norm = np.linalg.norm(command)
            if norm > c["max_speed"]:
                command *= c["max_speed"] / norm
            dv = command - vel
            dv_norm = np.linalg.norm(dv)
            if dv_norm > a_max * dt:
                dv *= a_max * dt / dv_norm
            vel = vel + dv
            robot = robot + vel * dt
            person = person + _person_velocity(person, robot, vel, t, c) * dt
        return trace


def _person_velocity(
    person: np.ndarray, robot: np.ndarray, robot_vel: np.ndarray, t: float, c: dict
) -> np.ndarray:
    """The person walks along +y, and steps around the robot if it is standing in the way.

    A moving robot is not avoided: the robot is responsible for not hitting the person.
    """
    speed = c["person_speed"]
    if t < c["person_delay"]:
        return np.zeros(2)
    rel = robot - person
    gap = float(np.linalg.norm(rel)) - ROBOT_RADIUS - PERSON_RADIUS
    robot_stopped = float(np.linalg.norm(robot_vel)) < 0.05
    if robot_stopped and gap < 0.4 and rel[1] > -0.25:
        away = -1.0 if rel[0] >= 0 else 1.0
        forward = 0.0 if gap < 0.15 else 0.5 * speed
        return np.array([away * speed, forward])
    return np.array([0.0, speed])


def cautious_policy(
    cruise: float = 1.0, slow_radius: float = 1.5, stop_radius: float = 0.6
) -> Any:
    """A reference policy: drive to the goal, slow near a detected person, stop if close."""

    def act(obs: dict[str, Any]) -> tuple[float, float]:
        pos = np.asarray(obs["position"])
        to_goal = np.asarray(obs["goal"]) - pos
        dist = float(np.linalg.norm(to_goal))
        if dist < 0.05:
            return (0.0, 0.0)
        speed = min(cruise, obs["max_speed"], dist)
        if obs["obstacle"] is not None:
            gap = float(np.linalg.norm(obs["obstacle"])) - ROBOT_RADIUS - PERSON_RADIUS
            if gap < stop_radius:
                speed = 0.0
            elif gap < slow_radius:
                speed *= (gap - stop_radius) / (slow_radius - stop_radius)
        direction = to_goal / dist
        return (float(direction[0] * speed), float(direction[1] * speed))

    return act


__all__ = ["DEFAULTS", "FLOOR_FRICTION", "HomeNavSim", "HomeNavWorld", "cautious_policy"]
