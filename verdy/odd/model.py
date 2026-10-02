"""Typed in-memory representation of an ODD document."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORIES = ("environment", "platform", "task", "sensors", "faults")
TYPES = ("continuous", "categorical", "boolean", "temporal")
NUMERIC_TYPES = ("continuous", "temporal")


@dataclass
class Parameter:
    name: str
    category: str
    type: str
    description: str = ""
    unit: str | None = None
    range: tuple[float, float] | None = None
    values: list[Any] | None = None
    weights: list[float] | None = None
    distribution: str | None = None
    default: Any = None
    grounding: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def is_numeric(self) -> bool:
        return self.type in NUMERIC_TYPES

    @property
    def domain(self) -> list[Any]:
        """Discrete values a non-numeric parameter can take."""
        if self.type == "boolean":
            return [False, True]
        return list(self.values or [])

    @property
    def approved(self) -> bool:
        """LLM-authored parameters count as approved only when a human signed off."""
        if self.provenance.get("source") == "llm":
            return bool(self.provenance.get("approved", False))
        return self.provenance.get("approved", True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Parameter:
        rng = data.get("range")
        return cls(
            name=data["name"],
            category=data["category"],
            type=data["type"],
            description=data.get("description", ""),
            unit=data.get("unit"),
            range=(float(rng[0]), float(rng[1])) if rng is not None else None,
            values=list(data["values"]) if data.get("values") is not None else None,
            weights=list(data["weights"]) if data.get("weights") is not None else None,
            distribution=data.get("distribution"),
            default=data.get("default"),
            grounding=dict(data.get("grounding") or {}),
            provenance=dict(data.get("provenance") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "category": self.category, "type": self.type}
        if self.description:
            out["description"] = self.description
        for key in ("unit", "distribution", "default"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.range is not None:
            out["range"] = list(self.range)
        if self.values is not None:
            out["values"] = list(self.values)
        if self.weights is not None:
            out["weights"] = list(self.weights)
        if self.grounding:
            out["grounding"] = dict(self.grounding)
        if self.provenance:
            out["provenance"] = dict(self.provenance)
        return out


@dataclass
class ODD:
    name: str
    version: str
    parameters: list[Parameter]
    description: str = ""
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    spec_version: str | None = None

    def __post_init__(self) -> None:
        self._index = {p.name: p for p in self.parameters}

    def __getitem__(self, name: str) -> Parameter:
        return self._index[name]

    def __contains__(self, name: object) -> bool:
        return name in self._index

    @property
    def parameter_names(self) -> list[str]:
        return [p.name for p in self.parameters]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ODD:
        return cls(
            name=data["name"],
            version=str(data["version"]),
            parameters=[Parameter.from_dict(p) for p in data["parameters"]],
            description=data.get("description", ""),
            constraints=list(data.get("constraints") or []),
            metadata=dict(data.get("metadata") or {}),
            spec_version=data.get("spec_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.spec_version:
            out["spec_version"] = self.spec_version
        out.update(name=self.name, version=self.version)
        if self.description:
            out["description"] = self.description
        out["parameters"] = [p.to_dict() for p in self.parameters]
        if self.constraints:
            out["constraints"] = list(self.constraints)
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out
