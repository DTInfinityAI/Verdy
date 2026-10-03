"""One level of the ontology tree as a Laya question.

The tree walker asks these questions at inference, and :mod:`verdy.finetune` writes the same
questions as training and evaluation data. Both use the functions below, so what Laya is
trained on and what it is asked can't drift apart.

A level question is a ``choice`` among a node's children, each rendered as its id with a
short gloss (label plus about ten words of definition, since options share Laya's head
token budget), plus ``none``.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from verdy.odd.ontology import NONE_LABEL, Node, Ontology, default_label

QUESTION_ID = "entry"
LEVEL_INSTRUCTIONS = (
    "A robot test engineer described an operating condition. Which branch of the parameter "
    "ontology contains the quantity they mean? Answer none if no branch does."
)
NONE_GLOSS = "none of these: a different quantity that needs a new entry"
GLOSS_WORDS = 10


def gloss(node: Node, words: int = GLOSS_WORDS) -> str:
    """``"Turbidity: suspended particles in water that reduce optical visibility"``."""
    label = node.label or default_label(node.name)
    definition = " ".join(node.description.split()[:words]).rstrip(".,;:")
    return f"{label}: {definition}" if definition else label


def level_options(ontology: Ontology, node: str | None,
                  exclude: Sequence[str] = ()) -> dict[str, str]:
    """``{child id: gloss}`` for a node's children (roots for ``None``)."""
    return {c.name: gloss(c) for c in ontology.children(node) if c.name not in exclude}


def level_question(options: dict[str, str]) -> dict[str, Any]:
    """A Laya ``choice`` question over ``options`` plus ``none`` (always last)."""
    criteria = dict(options)
    criteria[NONE_LABEL] = NONE_GLOSS
    return {"type": "choice", "instructions": LEVEL_INSTRUCTIONS, "criteria": criteria}


def phrase_state(phrase: str, proposed_name: str = "", unit: str = "") -> dict[str, Any]:
    """The state Laya reads: the words used, the extraction step's name for the quantity,
    and the unit the description gave. The approval log records exactly these three, so
    training states match inference states."""
    state: dict[str, Any] = {"mentioned_as": phrase}
    if proposed_name and proposed_name != phrase:
        state["proposed_name"] = proposed_name
    if unit:
        state["unit"] = unit
    return state
