"""Signal Temporal Logic specs and robustness scoring (via RTAMT).

Robustness is a signed margin: positive means the trace satisfies the spec with that much
room to spare, negative means it violates it by that much. Formulas use RTAMT's
discrete-time STL syntax, and time bounds are in seconds, e.g.
``always(dist_obstacle >= 0.0)`` or ``eventually[0:20](dist_goal <= 0.2)``.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import rtamt

from verdy.spec import load_schema

_KEYWORDS = {
    "always", "eventually", "historically", "once", "since", "until", "unless", "precedes",
    "and", "or", "not", "implies", "iff", "xor", "abs", "sqrt", "exp", "pow", "rise", "fall",
    "prev", "next", "true", "false", "G", "F", "H", "O", "S", "U", "X",
}
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BOUNDS = re.compile(r"\[[^\]]*\]")


class SpecError(ValueError):
    """Raised for malformed specs or traces that cannot be scored."""


def infer_signals(formula: str) -> list[str]:
    """Signal names referenced by a formula, in order of first use."""
    seen: list[str] = []
    for name in _IDENT.findall(_BOUNDS.sub("", formula)):
        if name not in _KEYWORDS and name not in seen:
            seen.append(name)
    return seen


@dataclass
class STLSpec:
    name: str
    formula: str
    signals: list[str] = field(default_factory=list)
    description: str = ""
    severity: str = "critical"
    monitor: str | None = None

    def __post_init__(self) -> None:
        if not self.signals:
            self.signals = infer_signals(self.formula)
            if self.monitor:
                self.signals += [s for s in infer_signals(self.monitor) if s not in self.signals]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> STLSpec:
        return cls(
            name=data["name"],
            formula=data["formula"],
            signals=list(data.get("signals") or []),
            description=data.get("description", ""),
            severity=data.get("severity", "critical"),
            monitor=data.get("monitor"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "formula": self.formula}
        out["signals"] = list(self.signals)
        if self.description:
            out["description"] = self.description
        out["severity"] = self.severity
        if self.monitor:
            out["monitor"] = self.monitor
        return out


def parse_specs(data: dict[str, Any]) -> list[STLSpec]:
    """Validate a specs document against the schema and return its specs."""
    try:
        jsonschema.validate(data, load_schema("stl_specs"))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise SpecError(f"invalid specs file at {where}: {exc.message}") from exc
    specs = [STLSpec.from_dict(s) for s in data["specs"]]
    names = [s.name for s in specs]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise SpecError("duplicate spec names: " + ", ".join(sorted(dupes)))
    for spec in specs:
        _compile(spec.formula, spec.signals, 0.1)  # fail fast on syntax errors
    return specs


def load_specs(path: str | Path) -> list[STLSpec]:
    from verdy.odd.loader import read_document

    return parse_specs(read_document(path))


def _compile(formula: str, signals: list[str], dt: float) -> Any:
    spec = rtamt.StlDiscreteTimeOfflineSpecification()
    for sig in signals:
        spec.declare_var(sig, "float")
    spec.set_sampling_period(dt, "s", 0.1)
    spec.spec = formula
    try:
        spec.parse()
    except Exception as exc:  # RTAMT raises several exception types for bad syntax
        raise SpecError(f"cannot parse STL formula {formula!r}: {exc}") from exc
    return spec


class STLEvaluator:
    """Scores traces against a list of specs."""

    def __init__(self, specs: list[STLSpec]) -> None:
        if not specs:
            raise SpecError("at least one spec is required")
        self.specs = specs
        self._cache: dict[tuple[str, float], Any] = {}

    @property
    def signals(self) -> set[str]:
        return {s for spec in self.specs for s in spec.signals}

    def _spec(self, spec: STLSpec, dt: float) -> Any:
        key = (spec.name, round(dt, 9))
        if key not in self._cache:
            self._cache[key] = _compile(spec.formula, spec.signals, dt)
        return self._cache[key]

    def robustness(self, trace: dict[str, list[float]]) -> dict[str, float]:
        """Robustness of each spec at time 0, keyed by spec name."""
        time = trace["time"]
        if len(time) < 2:
            raise SpecError("trace needs at least two samples")
        dt = (time[-1] - time[0]) / (len(time) - 1)
        out = {}
        for spec in self.specs:
            missing = [s for s in spec.signals if s not in trace]
            if missing:
                raise SpecError(f"spec {spec.name!r} needs missing signals: {', '.join(missing)}")
            data = {"time": list(time), **{s: list(trace[s]) for s in spec.signals}}
            result = self._spec(spec, dt).evaluate(data)
            value = float(result[0][1]) if result else math.nan
            out[spec.name] = value
        return out
