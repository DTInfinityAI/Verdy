from __future__ import annotations

import copy
from pathlib import Path

import pytest

from verdy.metrics import STLSpec
from verdy.odd import ODD, validate_odd

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

ODD_DOC = {
    "spec_version": "0.2.0",
    "name": "test-odd",
    "version": "1.0",
    "parameters": [
        {"name": "lighting", "category": "environment", "type": "continuous",
         "unit": "lux", "range": [20, 1000], "distribution": "loguniform",
         "grounding": {"sim": "lighting_lux"}},
        {"name": "floor_type", "category": "environment", "type": "categorical",
         "values": ["tile", "wood", "carpet"], "weights": [0.5, 0.3, 0.2]},
        {"name": "person_speed", "category": "environment", "type": "continuous",
         "unit": "m/s", "range": [0.3, 1.8], "distribution": "normal(1.0, 0.3)"},
        {"name": "person_delay", "category": "environment", "type": "temporal",
         "unit": "s", "range": [0, 6]},
        {"name": "wet", "category": "environment", "type": "boolean",
         "distribution": "bernoulli(0.2)"},
    ],
    "constraints": ["not (wet and floor_type == 'carpet')"],
}


@pytest.fixture
def odd_doc() -> dict:
    return copy.deepcopy(ODD_DOC)


@pytest.fixture
def odd(odd_doc) -> ODD:
    return validate_odd(odd_doc)


@pytest.fixture
def nav_specs() -> list[STLSpec]:
    return [
        STLSpec("no_collision", "always(dist_obstacle >= 0.0)", severity="critical"),
        STLSpec("slow_near_person", "always((dist_obstacle <= 0.5) implies (speed <= 0.3))",
                severity="major"),
        STLSpec("reach_goal", "eventually[0:20](dist_goal <= 0.2)", severity="minor"),
    ]
