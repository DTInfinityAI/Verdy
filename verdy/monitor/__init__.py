"""Runtime monitors: the same specs, evaluated on the robot as it runs.

On-robot monitoring can only look at the past, so each spec is monitored through its
``monitor`` formula (past-time STL, e.g. ``historically(dist_obstacle >= 0.0)``). When a
spec has no ``monitor`` formula, a top-level ``always(...)`` over a formula with no other
temporal operators is converted to ``historically(...)`` automatically; any other formula
needs an explicit ``monitor``.

Feed samples at the fixed rate given by ``dt``; RTAMT counts samples, so time bounds in
the formula are interpreted as ``dt`` seconds per sample.

.. code-block:: python

    monitor = RuntimeMonitor(specs, dt=0.1)
    for sample in robot_stream():                 # {"dist_obstacle": 0.8, "speed": 0.4}
        status = monitor.update(sample)
        if not status.ok:
            robot.safe_stop(status.violated)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import rtamt

from verdy.metrics.stl import SpecError, STLSpec

_TEMPORAL = re.compile(r"\b(always|eventually|historically|once|since|until|unless|prev|next)\b")
_ALWAYS = re.compile(r"^\s*always\s*\((.*)\)\s*$", re.S)


def monitor_formula(spec: STLSpec) -> str:
    """The past-time formula used to monitor ``spec`` at runtime."""
    if spec.monitor:
        return spec.monitor
    match = _ALWAYS.match(spec.formula)
    if match and _balanced(match.group(1)) and not _TEMPORAL.search(match.group(1)):
        return f"historically({match.group(1)})"
    raise SpecError(
        f"spec {spec.name!r} has no past-time 'monitor' formula and cannot be converted "
        "automatically; add one to the specs file"
    )


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


@dataclass
class MonitorStatus:
    step: int
    robustness: dict[str, float]

    @property
    def ok(self) -> bool:
        return all(r >= 0 for r in self.robustness.values())

    @property
    def violated(self) -> list[str]:
        return [name for name, r in self.robustness.items() if r < 0]


class RuntimeMonitor:
    def __init__(self, specs: list[STLSpec], dt: float, skip_unmonitorable: bool = False) -> None:
        self.dt = dt
        self.step = 0
        self._monitors: dict[str, tuple[Any, list[str]]] = {}
        self.skipped: list[str] = []
        for spec in specs:
            try:
                formula = monitor_formula(spec)
            except SpecError:
                if skip_unmonitorable:
                    self.skipped.append(spec.name)
                    continue
                raise
            online = rtamt.StlDiscreteTimeOnlineSpecification()
            for sig in spec.signals:
                online.declare_var(sig, "float")
            online.set_sampling_period(dt, "s", 0.1)
            online.spec = formula
            try:
                online.parse()
            except Exception as exc:
                raise SpecError(f"cannot monitor {spec.name!r} with {formula!r}: {exc}") from exc
            self._monitors[spec.name] = (online, spec.signals)

    def update(self, sample: dict[str, float]) -> MonitorStatus:
        """Add one sample of every signal and return the current robustness per spec."""
        out = {}
        for name, (online, signals) in self._monitors.items():
            try:
                values = [(s, float(sample[s])) for s in signals]
            except KeyError as exc:
                raise SpecError(f"sample is missing signal {exc.args[0]!r}") from None
            out[name] = float(online.update(self.step, values))
        self.step += 1
        return MonitorStatus(step=self.step - 1, robustness=out)


__all__ = ["MonitorStatus", "RuntimeMonitor", "monitor_formula"]
