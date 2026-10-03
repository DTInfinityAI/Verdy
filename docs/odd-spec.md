# ODD specification (v0.4.0)

An **Operational Design Domain** (ODD) describes the conditions a robot policy is expected
to operate in. Verdy samples test scenarios from it, measures how much of it a test
campaign covered, and states every verdict relative to it: a `PASS` means "safe enough
*within this ODD*", nothing more.

This document is the normative description of ODD documents. The machine-readable form is
the JSON Schema in [`verdy/spec/odd.schema.json`](../verdy/spec/odd.schema.json)
(`$id: https://dti.ai/verdy/spec/odd/0.4.0`); print it with `verdy schema odd`.

## Document format

An ODD is a YAML or JSON document. Unknown fields are errors, so typos are caught.

| Field | Type | Required | Description |
| --- | --- | :---: | --- |
| `spec_version` | string | | Meta-schema version the document targets, e.g. `0.4.0`. |
| `name` | string | ✅ | Name of the domain. |
| `version` | string | ✅ | Version of this ODD. Bump it whenever the domain changes. |
| `description` | string | | What the domain covers, in plain language. |
| `parameters` | list | ✅ | At least one [parameter](#parameters). |
| `constraints` | list of strings | | [Constraint expressions](#constraints) every scenario must satisfy. |
| `metadata` | object | | Free-form information (owner, links, ...). Not interpreted. |

## Parameters

Each parameter is one dimension along which conditions vary.

| Field | Type | Required | Description |
| --- | --- | :---: | --- |
| `name` | string | ✅ | Identifier: letters, digits and `_`, not starting with a digit. Unique within the ODD. |
| `description` | string | | One sentence on what the parameter means. |
| `category` | enum | ✅ | `environment`, `platform`, `task`, `sensors` or `faults`. |
| `type` | enum | ✅ | `continuous`, `temporal`, `categorical` or `boolean`. |
| `unit` | string | | Unit of measure, e.g. `lux`, `m/s`, `s`. |
| `range` | `[min, max]` | for numeric types | Closed interval of allowed values; `min < max`. |
| `values` | list | for `categorical` | Allowed values; must be unique. |
| `weights` | list of numbers ≥ 0 | | Relative frequency of each entry in `values`. |
| `distribution` | string | | Nominal [distribution](#distributions). |
| `default` | any | | Typical value; must lie in the domain. |
| `grounding` | object | | Where the parameter lives in each system: `sim` (simulator key) and `runtime` (on-robot source, e.g. a topic). |
| `provenance` | object | | Who defined the parameter: `source` (`human`, `llm`, `log` or `ontology`), `confidence` (0–1), `approved` (bool), `note`; for drafted parameters also `resolution`, `new_ontology_entry` and `also_mentioned_as` (see [provenance](#provenance-and-llm-authoring)). |

### Categories

| Category | Use for | Examples |
| --- | --- | --- |
| `environment` | The world around the robot | lighting, floor type, people, clutter |
| `platform` | The robot itself | speed limits, payload, battery level |
| `task` | What the robot is asked to do | goal distance, object type |
| `sensors` | Perception capabilities | detection range, camera resolution |
| `faults` | Degradations and failures | frame dropout, wheel slip, GPS loss |

### Types

| Type | Domain | Needs |
| --- | --- | --- |
| `continuous` | Real numbers in `range` | `range` |
| `temporal` | Times or durations in `range` (treated like `continuous`) | `range` |
| `categorical` | One of `values` | `values` |
| `boolean` | `true` or `false` | — |

`range` is ignored (with a warning) for non-numeric types, and `values` for numeric ones.

## Distributions

`distribution` gives how often each value occurs in deployment: the *nominal*
distribution. Failure probabilities are estimated under it, so it should reflect real
operating frequencies rather than how often you want to test something (samplers handle
that). Numeric distributions are always truncated to `range`.

| Type | `distribution` | Meaning |
| --- | --- | --- |
| continuous, temporal | `uniform` (default) | Uniform over `range` |
| | `normal(mu, sigma)` | Normal, truncated to `range`; `sigma > 0` |
| | `loguniform` | Log-uniform over `range`; needs `min > 0` |
| | `triangular(mode)` | Triangular over `range` peaking at `mode` |
| | `beta(a, b)` | Beta(a, b) scaled to `range`; `a, b > 0` |
| categorical | `uniform` (default) | Uniform over `values`, or proportional to `weights` if given |
| boolean | `bernoulli(p)` (default `p = 0.5`) | `true` with probability `p` |

## Constraints

Constraints exclude combinations that cannot occur, e.g. a speed governor that caps speed
on wet floors. Each is a Python-syntax boolean expression over parameter names:

```yaml
constraints:
  - "floor_type != 'wet_tile' or max_speed <= 1.0"
  - "not (night and lighting > 500)"
  - "abs(person_speed - 1.0) < 0.6 or person_type == 'child'"
```

Allowed: numbers, strings, `true`/`false`, parameter names, arithmetic (`+ - * / // % **`),
comparisons (chains like `0 < x < 1` work), `and`/`or`/`not`, `in`/`not in` with list
literals, `x if c else y`, and the functions `abs`, `min`, `max`, `sqrt`, `log`, `exp`.
Anything else (attribute access, other calls, comprehensions, assignment) is rejected, so
ODD files cannot run code. Every name must be a parameter of the ODD.

Samplers draw scenarios from the nominal distribution *conditioned on* the constraints.

## Provenance and LLM authoring

`verdy author "..."` drafts an ODD with Claude, resolving what the description mentions
against a parameter ontology first (see [ontology and resolvers](ontology.md)):

- A parameter taken from the ontology has `source: ontology`. Its `confidence` is the
  resolver's probability.
- A parameter Claude wrote because the ontology had no entry has `source: llm` and
  `new_ontology_entry: true`.
- `resolution` records how the wording was resolved: `resolver`, `model`, `decision` (entry
  name or `none`), `probability`, `candidate` (the extracted name), `phrase` (the words in
  the description), `proposed` (a rejected choice), `reason`, `ontology` (`name@version`),
  `embedder` and the top of the `shortlist` with scores.
- `also_mentioned_as` lists other phrases that resolved to the same parameter.
- With the `laya-tree` resolver, `resolution.path` lists the node and probability chosen at
  each level, and a miss has `resolution.placement`, the group below which Laya answered
  none. A new entry's `ontology_parent` is the group it is proposed under.

Every drafted parameter starts with `approved: false`. Unapproved `llm` and `ontology`
parameters are warnings in normal validation and errors with `verdy validate --strict`, so a
human has to review each one and set `approved: true` before strict pipelines accept the
ODD.

## Validation

`verdy validate odd.yaml` (or `verdy.odd.check_odd`) runs two passes:

1. **Schema:** structure, required fields, enums, unknown fields.
2. **Semantics:** unique names; numeric types have a valid `range`; categorical values are
   unique; `weights` match `values`; the distribution parses and fits the type and range;
   `default` is in the domain; constraints parse and reference only known parameters;
   approval status of drafted (`llm` and `ontology`) parameters.

## Example

```yaml
spec_version: 0.4.0
name: home-robot-kitchen-crossing
version: 0.1.0
parameters:
  - name: lighting
    category: environment
    type: continuous
    unit: lux
    range: [20, 1000]
    distribution: loguniform
    grounding: {sim: lighting_lux, runtime: /sensors/ambient_light/lux}
  - name: floor_type
    category: environment
    type: categorical
    values: [tile, wood, carpet, rug, wet_tile]
    weights: [0.35, 0.3, 0.2, 0.1, 0.05]
  - name: sensor_dropout
    category: faults
    type: continuous
    range: [0.0, 0.3]
    distribution: beta(1, 8)
constraints:
  - "floor_type != 'wet_tile' or max_speed <= 1.0"
```

The complete version is [`examples/home_robot/odd.yaml`](../examples/home_robot/odd.yaml).

## Versioning

The meta-schema follows semantic versioning. While it is `0.x`, minor versions may make
breaking changes. Changes from `0.3.0` (additive): provenance `ontology_parent`, and
`resolution.path` and `resolution.placement` from the tree walker. Changes from `0.2.0` (additive; every `0.2.0` document is valid `0.3.0`):
provenance `source: ontology`, `resolution`, `new_ontology_entry` and `also_mentioned_as`.
Changes from `0.1.0`: added `spec_version`, `description`, `metadata`,
parameter `description`, `weights`, `default` and `provenance.note`; unknown fields are
now rejected; `provenance.confidence` must be in [0, 1].
