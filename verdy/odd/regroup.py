"""LLM-proposed intermediate groups for ontology nodes with too many children.

Every node should have at most ``max_children`` children (default 15), so a Laya level
question keeps room for "none". When a node outgrows that, Claude proposes intermediate
groups. ``verdy ontology regroup`` writes them into a copy of the ontology as
``status: draft`` groups, with the children moved under them, for a human to review and
approve (set ``status: approved``, or edit or reject the groups).

Regrouping never invalidates the approval log: it stores phrase → leaf, and training
examples are regenerated from whatever the tree looks like at export time.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from verdy.odd.levels import gloss
from verdy.odd.ontology import NONE_LABEL, Ontology, OntologyGroup

MIN_GROUP_SIZE = 2

REGROUP_PROMPT = """\
You maintain a tree-shaped ontology of robot operating-condition parameters. A node has \
too many children for a classifier that chooses among a node's children at each level. \
Propose intermediate groups under that node so it ends up with at most {limit} children, \
and every new group has between {min_size} and {limit} children.

Group by what the quantities physically are (optical, motion, chemistry), not by how \
often they come up. Each group gets a snake_case id, a short label and a one-line \
definition that tells the members apart from their new siblings. Put each child in at \
most one group; children that fit no group stay where they are. Use only the given child \
ids, and new group ids that are not already taken."""

REGROUP_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "definition": {"type": "string"},
                    "children": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "label", "definition", "children"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["groups", "rationale"],
    "additionalProperties": False,
}


@dataclass
class ProposedGroup:
    id: str
    label: str
    definition: str
    children: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    node: str
    groups: list[ProposedGroup]
    rationale: str = ""
    model: str = ""

    def remaining(self, ontology: Ontology) -> list[str]:
        """Children that stay directly under the node."""
        moved = {c for g in self.groups for c in g.children}
        return [c.name for c in ontology.children(self.node) if c.name not in moved]


def over_limit(ontology: Ontology, max_children: int | None = None) -> list[str]:
    """Groups with more children than the limit, in file order."""
    limit = max_children or ontology.max_children
    return [g.name for g in ontology.groups if len(ontology.children(g.name)) > limit]


def check_proposal(ontology: Ontology, proposal: Proposal,
                   max_children: int | None = None) -> list[str]:
    """Problems with a proposal; empty when it can be applied."""
    limit = max_children or ontology.max_children
    errors = []
    children = {c.name for c in ontology.children(proposal.node)}
    seen: dict[str, str] = {}
    ids = set()
    for g in proposal.groups:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", g.id) or g.id == NONE_LABEL:
            errors.append(f"group id {g.id!r} must be snake_case and not 'none'")
        if ontology.has_node(g.id) or g.id in ids:
            errors.append(f"group id {g.id!r} is already taken")
        ids.add(g.id)
        if not MIN_GROUP_SIZE <= len(g.children) <= limit:
            errors.append(f"group {g.id!r} has {len(g.children)} children; "
                          f"needs {MIN_GROUP_SIZE} to {limit}")
        for c in g.children:
            if c not in children:
                errors.append(f"{c!r} is not a child of {proposal.node!r}")
            elif c in seen:
                errors.append(f"{c!r} is in both {seen[c]!r} and {g.id!r}")
            else:
                seen[c] = g.id
    total = len(proposal.remaining(ontology)) + len(proposal.groups)
    if total > limit:
        errors.append(f"{proposal.node!r} would still have {total} children, limit {limit}")
    return errors


def propose_groups(ontology: Ontology, node: str, *, client: Any = None,
                   model: str | None = None, max_children: int | None = None,
                   max_attempts: int = 3) -> Proposal:
    """Ask Claude for intermediate groups under ``node``; retry with the problems found."""
    from verdy.llm import DEFAULT_MODEL, LLMError, claude_client, create_json

    if not ontology.is_group(node):
        raise ValueError(f"{node!r} is not a group of {ontology.ref}")
    limit = max_children or ontology.max_children
    model = model or DEFAULT_MODEL
    if client is None:
        client = claude_client()
    path = " → ".join(ontology.path(node))
    listing = "\n".join(f"- {c.name}: {gloss(c, words=25)}"
                        + (" (group)" if isinstance(c, OntologyGroup) else "")
                        for c in ontology.children(node))
    taken = ", ".join(sorted(n.name for n in ontology.nodes))
    messages: list[dict[str, Any]] = [{"role": "user", "content": (
        f"Node {node!r} ({path}) has {len(ontology.children(node))} children, limit {limit}:"
        f"\n{listing}\n\nIds already taken: {taken}")}]
    system = REGROUP_PROMPT.format(limit=limit, min_size=MIN_GROUP_SIZE)
    errors: list[str] = []
    for _ in range(max_attempts):
        try:
            data, response = create_json(client, system=system, messages=messages,
                                         schema=REGROUP_SCHEMA, model=model, effort="high")
        except LLMError as exc:
            raise RuntimeError(f"regrouping {node!r} failed: {exc}") from exc
        proposal = Proposal(
            node=node, rationale=data.get("rationale", ""), model=model,
            groups=[ProposedGroup(g["id"], g["label"], g["definition"], list(g["children"]))
                    for g in data["groups"]])
        errors = check_proposal(ontology, proposal, limit)
        if not errors:
            return proposal
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": "Fix these problems and return the full "
                         "proposal again:\n- " + "\n- ".join(errors)})
    raise ValueError(f"no valid grouping for {node!r}: " + "; ".join(errors))


def apply_proposal(ontology: Ontology, proposal: Proposal) -> Ontology:
    """A copy of the ontology with the proposed groups added as drafts."""
    errors = check_proposal(ontology, proposal)
    if errors:
        raise ValueError("; ".join(errors))
    out = copy.deepcopy(ontology)
    for g in proposal.groups:
        out.add(OntologyGroup(name=g.id, label=g.label, description=g.definition,
                              parent=proposal.node, status="draft"))
        for child in g.children:
            out.node(child).parent = g.id
    # Keep each new group next to its members in the file: place it before its first child.
    order = {n.name: i for i, n in enumerate(out.nodes)}
    first = {g.id: min(order[c] for c in g.children) - 0.5 for g in proposal.groups}
    out.nodes.sort(key=lambda n: first.get(n.name, order[n.name]))
    out._index()
    return out
