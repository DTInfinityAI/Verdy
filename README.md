# Verdy

**A driving test for robot AI.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Status: early development](https://img.shields.io/badge/status-early%20development-orange.svg)

Verdy tells you whether a robot policy is safe to deploy, and how confident you can be.

Describe your operating conditions, and Verdy:

1. **Samples scenarios** across that operating domain
2. **Runs your policy** in simulation or on recorded logs
3. **Scores every run** against formal safety specifications
4. **Returns a verdict** of `PASS`, `FAIL`, or `INCONCLUSIVE`, with confidence bounds and a reproducible evidence trail

---

## Table of contents

- [How it works](#how-it-works)
- [Key features](#key-features)
- [Verdicts](#verdicts)
- [Getting started](#getting-started)
- [Defining an ODD](#defining-an-odd)
- [Writing a backend](#writing-a-backend)
- [Project structure](#project-structure)
- [Project status](#project-status)
- [License](#license)

---

## How it works

```
 ODD spec ──▶ Scenario sampler ──▶ Execution backend ──▶ STL scoring ──▶ Statistical verdict
 (odd/,        (sampler/)            (backends/)          (metrics/)      (verdict/)
  spec/)                                                                       │
                                                                               ▼
                                                                  Evidence ledger (ledger/)
```

| Stage | What it does |
| --- | --- |
| **ODD spec** | A typed, versioned description of the *Operational Design Domain*: the conditions the robot is expected to operate in. |
| **Scenario sampler** | Draws concrete scenarios from the ODD using stratified coverage, log replay, or failure-seeking importance sampling. |
| **Execution backend** | Turns each scenario into a runnable environment and rolls out the policy, producing a time-stamped signal trace. |
| **STL scoring** | Evaluates each trace against Signal Temporal Logic safety specs (via [RTAMT](https://github.com/nickovic/rtamt)) and reports robustness margins. |
| **Statistical verdict** | Aggregates the runs into failure-probability bounds, coverage, and a final decision. |
| **Evidence ledger** | Hashes inputs and records everything needed to reproduce the result as a signed report. |

---

## Key features

| Feature | Description |
| --- | --- |
| **ODD specs** | Describe operating domains in a typed, versioned schema, with LLM-assisted authoring. |
| **Scenario sampling** | Coverage-driven sampling, log replay, and failure-seeking importance sampling. |
| **Backend-agnostic** | Works with SceneSmith, custom simulators, log replay, and hardware-in-the-loop (HIL). |
| **STL scoring** | Signal Temporal Logic scoring that reports robustness margins, not just pass/fail. |
| **Statistical verdicts** | Failure-probability bounds and coverage reports. |
| **Runtime monitors** | The same specs, deployed on the robot. |

---

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | The policy met the safety specs across the sampled domain within the stated confidence bounds. |
| `FAIL` | One or more runs violated a safety spec. |
| `INCONCLUSIVE` | Not enough evidence yet to reach a confident `PASS` or `FAIL`. |

Every verdict comes with confidence bounds and a reproducible evidence trail, so results can be audited and re-run.

---

## Getting started

**Requirements:** Python 3.10 or newer.

```bash
git clone https://github.com/DTInfinityAI/Verdy.git
cd Verdy

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install jsonschema numpy scipy rtamt
```

Run Python from the repository root so that the `verdy` package can be imported:

```python
from verdy.backends.base import Backend
```

> **Note:** `pip install ./verdy` does not work yet. The package uses a flat layout with several top-level packages, which setuptools refuses to auto-discover. Install the dependencies directly as shown above until packaging is fixed.

---

## Defining an ODD

An ODD is a JSON document validated against the meta-schema in [`verdy/spec/odd.schema.json`](verdy/spec/odd.schema.json) (JSON Schema draft 2020-12, spec version `0.1.0`).

**Top-level fields**

| Field | Required | Description |
| --- | :---: | --- |
| `name` | ✅ | Name of the operating domain. |
| `version` | ✅ | Version of this ODD. |
| `parameters` | ✅ | List of parameters that describe the domain (see below). |
| `constraints` | | List of constraint expressions, as strings. |

**Parameter fields**

| Field | Required | Description |
| --- | :---: | --- |
| `name` | ✅ | Parameter name. |
| `category` | ✅ | One of `environment`, `platform`, `task`, `sensors`, `faults`. |
| `type` | ✅ | One of `continuous`, `categorical`, `boolean`, `temporal`. |
| `unit` | | Unit of measure, e.g. `lux`. |
| `range` | | `[min, max]` for continuous parameters. |
| `values` | | Allowed values for categorical parameters. |
| `distribution` | | Name of the sampling distribution. |
| `grounding` | | How the parameter maps to the real system: `runtime` and `sim`. |
| `provenance` | | Where the parameter came from: `source` (`llm`, `human`, `log`), `confidence`, `approved`. |

**Example**

```python
import json
import jsonschema

schema = json.load(open("verdy/spec/odd.schema.json"))

odd = {
    "name": "kitchen-tidy",
    "version": "0.1.0",
    "parameters": [
        {
            "name": "lighting",
            "category": "environment",
            "type": "continuous",
            "unit": "lux",
            "range": [50, 1000],
        },
        {
            "name": "floor_type",
            "category": "environment",
            "type": "categorical",
            "values": ["tile", "carpet", "wood"],
        },
    ],
}

jsonschema.validate(odd, schema)  # raises ValidationError if the ODD is invalid
```

---

## Writing a backend

Every execution backend implements the abstract `Backend` class in [`verdy/backends/base.py`](verdy/backends/base.py):

| Method | Description |
| --- | --- |
| `build(scenario: dict) -> object` | Realize a sampled scenario as a runnable environment. |
| `rollout(env, policy, seed: int) -> dict` | Run the policy and return a time-stamped trace of signals. |

```python
from verdy.backends.base import Backend


class MySimBackend(Backend):
    def build(self, scenario: dict) -> object:
        ...  # create and configure your simulator from the scenario

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        ...  # step the policy in env and return {"time": [...], "<signal>": [...]}
```

---

## Project structure

```
Verdy/
├── LICENSE
├── README.md
└── verdy/
    ├── pyproject.toml        # Package metadata and dependencies
    ├── spec/                 # ODD meta-schema (JSON Schema), published as a standalone versioned spec
    │   └── odd.schema.json
    ├── odd/                  # ODD parsing, validation, and LLM-assisted authoring
    ├── sampler/              # Scenario generation: stratified coverage, log replay, importance sampling
    ├── backends/             # Execution backends behind a common adapter
    │   ├── base.py           #   Abstract Backend interface
    │   ├── scenesmith/       #   SceneSmith simulator adapter
    │   └── replay/           #   Recorded-log replay adapter
    ├── metrics/              # Signal Temporal Logic specs and robustness scoring (via RTAMT)
    ├── verdict/              # Failure-probability bounds, coverage, and PASS/FAIL/INCONCLUSIVE rules
    ├── ledger/               # Input hashing, reproducibility records, and signed evidence reports
    ├── examples/             # End-to-end demos, starting with a home-robot task on SceneSmith
    └── docs/                 # Documentation
```

---

## Project status

Verdy is in **early development**. The project structure and core interfaces are in place, and most modules are still to be implemented.

| Component | Status |
| --- | --- |
| ODD meta-schema (`spec/`) | ✅ Draft `0.1.0` |
| Backend interface (`backends/base.py`) | ✅ Defined |
| ODD parsing and LLM authoring (`odd/`) | 🚧 Planned |
| Scenario sampler (`sampler/`) | 🚧 Planned |
| SceneSmith and replay backends | 🚧 Planned |
| STL scoring (`metrics/`) | 🚧 Planned |
| Statistical verdicts (`verdict/`) | 🚧 Planned |
| Evidence ledger (`ledger/`) | 🚧 Planned |
| Home-robot example (`examples/`) | 🚧 Planned |
| Runtime monitors | 🚧 Planned |

Built by **DeepThought Infinity (DTI.ai)**.

---

## License

Verdy is licensed under the [Apache License 2.0](LICENSE).
