"""Stand-ins for human operators, so the improvement loop runs end to end in a demo.

In a real deployment these come from people: demonstrations are recorded teleoperation
sessions, and preferences come from operators comparing pairs of test runs (for example
through ``verdy.improve.FileLabeler``). Here a scripted "operator" plays both roles.
"""
from __future__ import annotations

from policy import CautiousNavigator


def expert_policy() -> CautiousNavigator:
    """How an experienced operator drives: unhurried, brakes early, remembers people."""
    return CautiousNavigator(cruise=0.75, slow_radius=2.6, stop_radius=0.95, memory=0.7)


def _quality(ep) -> float:
    f = ep.features
    clearance = min(f.get("dist_obstacle:min", 0.0), 1.5)  # keep a wide berth
    smooth = f.get("speed:rough", 0.0)                      # no jerky speed changes
    arrived = 1.0 if f.get("dist_goal:min", 9.0) < 0.2 else 0.0
    return 2.0 * clearance - 1.0 * smooth + 0.5 * arrived


def prefers_smooth_and_safe(a, b) -> str:
    """Prefer the run with more clearance and smoother motion that still arrives."""
    qa, qb = _quality(a), _quality(b)
    if abs(qa - qb) < 0.05:
        return "tie"
    return "a" if qa > qb else "b"
