"""Parameter ontology: canonical ODD parameters shared across ODDs and customers.

An ontology entry is a parameter definition that has been reviewed once and is reused
everywhere: same name, unit, physical bounds, nominal distribution and grounding. ODD
authoring resolves what a description mentions ("murky water") to an entry
(``turbidity``) instead of letting a language model invent a new name each time.

File format (YAML or JSON)::

    name: core
    version: 0.1.0
    entries:
      - name: turbidity
        description: Suspended particles in water that reduce visibility.
        category: environment
        type: continuous
        unit: NTU
        range: [0, 100]
        distribution: loguniform     # optional nominal distribution
        synonyms: [murky water, water clarity, visibility underwater]
        grounding: {sim: water_turbidity}

The bundled ``core`` ontology is a starting point; extend it with ``verdy ontology add``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from verdy.odd.model import Parameter
from verdy.spec import load_schema

NONE_LABEL = "none"


class OntologyError(ValueError):
    """Raised for an invalid ontology file."""


@dataclass
class OntologyEntry:
    name: str
    category: str
    type: str
    description: str = ""
    unit: str | None = None
    range: tuple[float, float] | None = None
    values: list[Any] | None = None
    distribution: str | None = None
    synonyms: list[str] = field(default_factory=list)
    grounding: dict[str, str] = field(default_factory=dict)

    @property
    def is_numeric(self) -> bool:
        return self.type in ("continuous", "temporal")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OntologyEntry:
        rng = data.get("range")
        return cls(
            name=data["name"],
            category=data["category"],
            type=data["type"],
            description=data.get("description", ""),
            unit=data.get("unit"),
            range=(float(rng[0]), float(rng[1])) if rng is not None else None,
            values=list(data["values"]) if data.get("values") is not None else None,
            distribution=data.get("distribution"),
            synonyms=list(data.get("synonyms") or []),
            grounding=dict(data.get("grounding") or {}),
        )

    @classmethod
    def from_parameter(cls, p: Parameter, synonyms: list[str] | None = None) -> OntologyEntry:
        return cls(
            name=p.name, category=p.category, type=p.type, description=p.description,
            unit=p.unit, range=p.range, values=p.values, distribution=p.distribution,
            synonyms=list(synonyms or []), grounding=dict(p.grounding),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.description:
            out["description"] = self.description
        out["category"] = self.category
        out["type"] = self.type
        if self.unit:
            out["unit"] = self.unit
        if self.range is not None:
            out["range"] = list(self.range)
        if self.values is not None:
            out["values"] = list(self.values)
        if self.distribution:
            out["distribution"] = self.distribution
        if self.synonyms:
            out["synonyms"] = list(self.synonyms)
        if self.grounding:
            out["grounding"] = dict(self.grounding)
        return out

    def text(self) -> str:
        """The text that represents this entry for retrieval and resolution."""
        parts = [self.name.replace("_", " ")]
        if self.description:
            parts.append(self.description)
        if self.unit:
            parts.append(f"unit {self.unit}")
        if self.synonyms:
            parts.append("also called: " + ", ".join(self.synonyms))
        return ". ".join(parts)


@dataclass
class Ontology:
    name: str
    version: str
    entries: list[OntologyEntry]
    description: str = ""

    def __post_init__(self) -> None:
        self._by_name = {e.name: e for e in self.entries}

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __getitem__(self, name: str) -> OntologyEntry:
        return self._by_name[name]

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def ref(self) -> str:
        """``name@version``, recorded in provenance."""
        return f"{self.name}@{self.version}"

    def add(self, entry: OntologyEntry, *, replace: bool = False) -> None:
        if entry.name in self._by_name and not replace:
            raise OntologyError(f"entry {entry.name!r} already exists")
        if entry.name == NONE_LABEL:
            raise OntologyError(f"{NONE_LABEL!r} is reserved for 'no matching entry'")
        self.entries = [e for e in self.entries if e.name != entry.name] + [entry]
        self._by_name[entry.name] = entry

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ontology:
        errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise OntologyError("; ".join(
                f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
        entries = [OntologyEntry.from_dict(e) for e in data["entries"]]
        names = [e.name for e in entries]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise OntologyError(f"duplicate entries: {', '.join(dupes)}")
        if NONE_LABEL in names:
            raise OntologyError(f"{NONE_LABEL!r} is reserved for 'no matching entry'")
        for e in entries:
            if e.is_numeric and (e.range is None or e.range[0] >= e.range[1]):
                raise OntologyError(f"{e.name}: numeric entries need range [min, max], min < max")
            if e.type == "categorical" and not e.values:
                raise OntologyError(f"{e.name}: categorical entries need values")
        return cls(name=data["name"], version=str(data["version"]), entries=entries,
                   description=data.get("description", ""))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.description:
            out["description"] = self.description
        out["entries"] = [e.to_dict() for e in self.entries]
        return out


def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(load_schema("ontology"))


BUNDLED = ("core",)


def load_ontology(ref: str | Path) -> Ontology:
    """Load a bundled ontology by name (``core``) or an ontology file by path."""
    if isinstance(ref, str) and ref in BUNDLED:
        text = resources.files("verdy.spec").joinpath(f"ontology_{ref}.yaml").read_text("utf-8")
    else:
        path = Path(ref)
        if not path.is_file():
            raise FileNotFoundError(
                f"no ontology file {path} (bundled ontologies: {', '.join(BUNDLED)})")
        text = path.read_text("utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise OntologyError("an ontology must be a mapping with 'name', 'version', 'entries'")
    return Ontology.from_dict(data)


def save_ontology(ontology: Ontology, path: str | Path) -> None:
    path = Path(path)
    data = ontology.to_dict()
    if path.suffix == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")
