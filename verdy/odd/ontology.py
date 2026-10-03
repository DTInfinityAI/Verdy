"""Parameter ontology: a versioned tree of canonical ODD parameters.

The ontology is the single source of truth for ODD parameter names. Three consumers read it:

- ODD authoring resolves what a description mentions ("murky water") to a leaf
  (``turbidity``) instead of letting a language model invent a name.
- The skill renderer (:mod:`verdy.odd.skill`) turns it into ``SKILL.md`` plus one reference
  file per branch for the LLM.
- The Laya tree walker (:class:`verdy.odd.resolve.LayaTreeResolver`) descends it one level at
  a time, and its fine-tuning data (:mod:`verdy.finetune`) is generated from it.

File format (YAML or JSON), a flat list of nodes linked by ``parent``::

    name: core
    version: 0.2.0
    max_children: 15            # structural rule checked by `verdy ontology validate`
    nodes:
      - id: environment         # roots are the ODD categories
        label: Environment
        definition: The world around the robot.
      - id: water
        parent: environment
        label: Water
        definition: Conditions of water the robot works in or near.
      - id: water_optical
        parent: water
        label: Optical
        definition: How well light travels through the water.
      - id: turbidity           # a leaf: it has a type, so it is an ODD parameter
        parent: water_optical
        label: Turbidity
        definition: Suspended particles in water that reduce optical visibility.
        aliases: [murky water, silt, water clarity]
        type: continuous
        unit: NTU
        range: [0.1, 1000]      # physical bounds; an ODD narrows them
        distribution: loguniform
        grounding: {sim: water_turbidity, runtime: /sensors/turbidity/ntu}
        status: approved        # or draft

A node with a ``type`` is a leaf (a parameter); any other node is a group. Ids are unique
across the tree, and the category of a leaf is its root. Every node should have at most
``max_children`` children (default 15), which leaves room for "none of these" within Laya's
option budget. The flat format of Verdy 0.6 (``entries`` with a ``category``) still loads:
each entry becomes a leaf under its category.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from verdy.odd.model import CATEGORIES, Parameter
from verdy.spec import load_schema

NONE_LABEL = "none"
DEFAULT_MAX_CHILDREN = 15
STATUSES = ("draft", "approved")


class OntologyError(ValueError):
    """Raised for an invalid ontology file."""


@dataclass
class OntologyGroup:
    """An internal node: a branch of the tree such as ``water`` or ``water_optical``."""

    name: str
    label: str = ""
    description: str = ""
    parent: str | None = None
    synonyms: list[str] = field(default_factory=list)
    status: str = "approved"

    is_leaf = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.name}
        if self.parent:
            out["parent"] = self.parent
        out["label"] = self.label or default_label(self.name)
        if self.description:
            out["definition"] = self.description
        if self.synonyms:
            out["aliases"] = list(self.synonyms)
        if self.status != "approved":
            out["status"] = self.status
        return out

    def text(self) -> str:
        parts = [self.label or default_label(self.name)]
        if self.description:
            parts.append(self.description)
        if self.synonyms:
            parts.append("also: " + ", ".join(self.synonyms))
        return ". ".join(parts)


@dataclass
class OntologyEntry:
    """A leaf: a canonical ODD parameter."""

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
    label: str = ""
    parent: str | None = None
    status: str = "approved"

    is_leaf = True

    @property
    def is_numeric(self) -> bool:
        return self.type in ("continuous", "temporal")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OntologyEntry:
        """A leaf from a node (``id``/``definition``/``aliases``) or a 0.6 flat entry."""
        rng = data.get("range")
        name = data.get("id", data.get("name"))
        return cls(
            name=name,
            category=data.get("category", ""),
            type=data["type"],
            description=data.get("definition", data.get("description", "")),
            unit=data.get("unit"),
            range=(float(rng[0]), float(rng[1])) if rng is not None else None,
            values=list(data["values"]) if data.get("values") is not None else None,
            distribution=data.get("distribution"),
            synonyms=list(data.get("aliases", data.get("synonyms")) or []),
            grounding=dict(data.get("grounding") or {}),
            label=data.get("label", ""),
            parent=data.get("parent") or data.get("category"),
            status=data.get("status", "approved"),
        )

    @classmethod
    def from_parameter(cls, p: Parameter, synonyms: list[str] | None = None, *,
                       parent: str | None = None, status: str = "approved") -> OntologyEntry:
        return cls(
            name=p.name, category=p.category, type=p.type, description=p.description,
            unit=p.unit, range=p.range, values=p.values, distribution=p.distribution,
            synonyms=list(synonyms or []), grounding=dict(p.grounding),
            parent=parent or p.category, status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.name}
        if self.parent:
            out["parent"] = self.parent
        out["label"] = self.label or default_label(self.name)
        if self.description:
            out["definition"] = self.description
        if self.synonyms:
            out["aliases"] = list(self.synonyms)
        out["type"] = self.type
        if self.unit:
            out["unit"] = self.unit
        if self.range is not None:
            out["range"] = list(self.range)
        if self.values is not None:
            out["values"] = list(self.values)
        if self.distribution:
            out["distribution"] = self.distribution
        if self.grounding:
            out["grounding"] = dict(self.grounding)
        if self.status != "approved":
            out["status"] = self.status
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


Node = OntologyGroup | OntologyEntry


def default_label(name: str) -> str:
    """``"water_optical"`` → ``"Water optical"``: the label of a node that has none."""
    return name.replace("_", " ").capitalize()


@dataclass
class Ontology:
    name: str
    version: str
    nodes: list[Node]
    description: str = ""
    max_children: int = DEFAULT_MAX_CHILDREN

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        self._by_name: dict[str, Node] = {}
        self._children: dict[str | None, list[Node]] = {}
        for n in self.nodes:
            self._by_name[n.name] = n
            self._children.setdefault(n.parent, []).append(n)
        for e in self.entries:
            path = self.path(e.name, strict=False)
            e.category = path[0] if path else e.category

    # --- lookup ---------------------------------------------------------------------

    @property
    def entries(self) -> list[OntologyEntry]:
        """The leaves, in file order."""
        return [n for n in self.nodes if isinstance(n, OntologyEntry)]

    @property
    def groups(self) -> list[OntologyGroup]:
        return [n for n in self.nodes if isinstance(n, OntologyGroup)]

    def __contains__(self, name: str) -> bool:
        """Whether a *leaf* with this id exists."""
        return isinstance(self._by_name.get(name), OntologyEntry)

    def __getitem__(self, name: str) -> OntologyEntry:
        node = self._by_name.get(name)
        if not isinstance(node, OntologyEntry):
            raise KeyError(name)
        return node

    def __len__(self) -> int:
        return len(self.entries)

    def node(self, name: str) -> Node:
        return self._by_name[name]

    def has_node(self, name: str) -> bool:
        return name in self._by_name

    def is_group(self, name: str | None) -> bool:
        return isinstance(self._by_name.get(name or ""), OntologyGroup)

    def children(self, name: str | None) -> list[Node]:
        """Children of a node, in file order; ``None`` gives the roots."""
        return list(self._children.get(name, []))

    @property
    def roots(self) -> list[Node]:
        return self.children(None)

    def path(self, name: str, *, strict: bool = True) -> list[str]:
        """Ids from the root down to ``name``, inclusive."""
        out: list[str] = []
        seen: set[str] = set()
        cur: str | None = name
        while cur is not None:
            if cur in seen or cur not in self._by_name:
                if strict:
                    raise OntologyError(f"{name}: broken parent chain at {cur!r}")
                return []
            seen.add(cur)
            out.append(cur)
            cur = self._by_name[cur].parent
        return out[::-1]

    def subtree(self, name: str) -> list[Node]:
        """``name`` and its descendants, depth first."""
        out = [self.node(name)]
        for child in self.children(name):
            out.extend(self.subtree(child.name))
        return out

    @property
    def ref(self) -> str:
        """``name@version``, recorded in provenance."""
        return f"{self.name}@{self.version}"

    @property
    def sha256(self) -> str:
        """Digest of the canonical JSON form; changes with any edit."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    # --- editing --------------------------------------------------------------------

    def add(self, node: Node, *, replace: bool = False) -> None:
        if node.name == NONE_LABEL:
            raise OntologyError(f"{NONE_LABEL!r} is reserved for 'no matching entry'")
        if node.name in self._by_name and not replace:
            raise OntologyError(f"node {node.name!r} already exists")
        if node.parent is not None and not isinstance(self._by_name.get(node.parent),
                                                      OntologyGroup):
            raise OntologyError(f"{node.name}: parent {node.parent!r} is not a group")
        if node.parent is None and isinstance(node, OntologyEntry):
            raise OntologyError(f"{node.name}: a leaf needs a parent")
        self.nodes = [n for n in self.nodes if n.name != node.name] + [node]
        self._index()

    # --- validation -----------------------------------------------------------------

    def check(self, max_children: int | None = None) -> tuple[list[str], list[str]]:
        """Structural errors and warnings (``verdy ontology validate``)."""
        limit = max_children or self.max_children
        errors: list[str] = []
        warnings: list[str] = []
        for parent, kids in self._children.items():
            if len(kids) > limit:
                where = parent or "<roots>"
                errors.append(
                    f"{where}: {len(kids)} children, limit {limit}; add an intermediate group "
                    "so Laya keeps room for 'none' among its options (verdy ontology regroup "
                    "proposes some)")
        for g in self.groups:
            if not self._children.get(g.name):
                warnings.append(f"{g.name}: group has no children")
        drafts = [n.name for n in self.nodes if n.status == "draft"]
        if drafts:
            warnings.append(f"{len(drafts)} draft nodes: {', '.join(drafts[:10])}"
                            + (" ..." if len(drafts) > 10 else ""))
        return errors, warnings

    # --- (de)serialisation ----------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ontology:
        if "entries" in data and "nodes" not in data:
            data = _from_flat(data)
        errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise OntologyError("; ".join(
                f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
        nodes: list[Node] = []
        for raw in data["nodes"]:
            if "type" in raw:
                nodes.append(OntologyEntry.from_dict(raw))
            else:
                nodes.append(OntologyGroup(
                    name=raw["id"], label=raw.get("label", ""),
                    description=raw.get("definition", ""), parent=raw.get("parent"),
                    synonyms=list(raw.get("aliases") or []),
                    status=raw.get("status", "approved")))
        names = [n.name for n in nodes]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise OntologyError(f"duplicate ids: {', '.join(dupes)}")
        if NONE_LABEL in names:
            raise OntologyError(f"{NONE_LABEL!r} is reserved for 'no matching entry'")
        by_name = {n.name: n for n in nodes}
        for n in nodes:
            if n.parent is None:
                if n.name not in CATEGORIES:
                    raise OntologyError(
                        f"{n.name}: roots must be ODD categories ({', '.join(CATEGORIES)})")
                if isinstance(n, OntologyEntry):
                    raise OntologyError(f"{n.name}: a leaf needs a parent")
            elif n.parent not in by_name:
                raise OntologyError(f"{n.name}: unknown parent {n.parent!r}")
            elif isinstance(by_name[n.parent], OntologyEntry):
                raise OntologyError(f"{n.name}: parent {n.parent!r} is a leaf")
        onto = cls(name=data["name"], version=str(data["version"]), nodes=nodes,
                   description=data.get("description", ""),
                   max_children=int(data.get("max_children", DEFAULT_MAX_CHILDREN)))
        for n in nodes:
            onto.path(n.name)  # raises on cycles
        for e in onto.entries:
            if e.is_numeric and (e.range is None or e.range[0] >= e.range[1]):
                raise OntologyError(f"{e.name}: numeric entries need range [min, max], min < max")
            if e.type == "categorical" and not e.values:
                raise OntologyError(f"{e.name}: categorical entries need values")
        return onto

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.description:
            out["description"] = self.description
        if self.max_children != DEFAULT_MAX_CHILDREN:
            out["max_children"] = self.max_children
        out["nodes"] = [n.to_dict() for n in self.nodes]
        return out


def _from_flat(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a Verdy 0.6 flat ontology (``entries`` with ``category``) to nodes."""
    entries = data.get("entries") or []
    used = [c for c in CATEGORIES if any(e.get("category") == c for e in entries)]
    nodes: list[dict[str, Any]] = [{"id": c, "label": c.capitalize()} for c in used]
    for e in entries:
        node = {k: v for k, v in e.items() if k not in ("name", "category", "description",
                                                       "synonyms")}
        node["id"] = e.get("name")
        if e.get("category") is not None:
            node["parent"] = e["category"]
        if e.get("description"):
            node["definition"] = e["description"]
        if e.get("synonyms"):
            node["aliases"] = e["synonyms"]
        nodes.append(node)
    out = {k: v for k, v in data.items() if k != "entries"}
    out["nodes"] = nodes
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
        raise OntologyError("an ontology must be a mapping with 'name', 'version', 'nodes'")
    return Ontology.from_dict(data)


def save_ontology(ontology: Ontology, path: str | Path) -> None:
    path = Path(path)
    data = ontology.to_dict()
    if path.suffix == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")
