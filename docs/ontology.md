# Ontology and resolvers

`verdy author` doesn't let a language model invent parameter names for things your team
has already defined. It resolves the description against a **parameter ontology**:
canonical names, units, physical bounds, distributions and grounding. Claude only writes
the parameters the ontology doesn't have yet. This gives the same names across ODDs and
customers, and a reusable ontology that grows with every approved ODD. It also cuts LLM
calls.

## Pipeline: LLM → shortlist → resolver → LLM → human

```text
"murky water near the jacket legs, strong current"
   │
   ▼ 1. LLM extracts     candidates with ranges:  water_clarity [5, 50] NTU ("murky water"),
   │                     current [1, 3] knots ("strong current"), leg_spacing [2, 6] m
   ▼ 2. Shortlist        top-k ontology entries per candidate (embeddings, default k = 15)
   ▼ 3. Resolver         turbidity (p = 0.93), current_speed (p = 0.88), none (p = 0.81)
   ▼ 4. LLM authors      definitions only for the misses → jacket_leg_spacing,
   │                     flagged new_ontology_entry
   ▼ 5. Human approves   provenance.approved: true, as for every drafted parameter
```

1. **Extract.** Claude turns the description into candidate parameters, each with the
   phrase that mentions it, a type, a unit and a range. A classifier can't do this, because
   it has to generate.
2. **Shortlist.** An embedder ranks the ontology entries against each candidate and keeps
   the top `k`. An exact name or synonym hit always ranks first.
3. **Resolve.** A resolver picks the matching entry or `none`, with a probability. A match
   below `--min-probability` (default 0.5) counts as `none`, and the rejected choice is
   recorded as `proposed`.
4. **Author misses.** Claude drafts full definitions (description, range, distribution) only
   for candidates resolved to `none`. If there are no misses, it makes no call.
5. **Approve.** Every drafted parameter starts with `approved: false`, so `verdy validate
   --strict` fails until a human reviews it.

Matched parameters take the entry's name, description, unit, distribution and grounding.
The range is the candidate's own range, clipped to the entry's physical bounds. If the units
differ ("knots" against `m/s`), Verdy keeps the entry's bounds and adds a `note` asking for
a converted, narrower range. Verdy doesn't convert units itself. Constraints the
extraction wrote are renamed to the resolved names. A constraint that names a parameter
that was dropped is removed and listed in `metadata.authoring.dropped_constraints`.

## Resolvers

| Resolver | How it decides | Needs |
| --- | --- | --- |
| `exact` (default) | Normalised string match of the candidate's name or phrase against entry names and synonyms. Probability 1.0 on a hit. | Nothing |
| `laya` | [Laya](https://github.com/NandhaKishorM/laya), an open-weight non-autoregressive decision model, runs locally. It answers one typed `choice` question per candidate, whose options are the shortlist plus `none`. The probability is Laya's probability for its choice. | `pip install "verdy[laya]"` (pulls in PyTorch; Laya downloads its weights from Hugging Face on first use). No API key. |
| `llm` | Claude resolves all candidates in one structured-output call, giving a probability and a reason for each. | `pip install "verdy[llm]"` and the `ANTHROPIC_API_KEY` secret |
| `module:attribute` | Your own subclass of `verdy.odd.resolve.Resolver`, or a factory for one | Your code |

```console
$ verdy author "ROV inspection: murky water near the jacket legs, strong current" \
    --resolver laya -o odd.draft.yaml
Draft ODD with 3 parameters written to odd.draft.yaml.
Resolved against core@0.1.0 with the laya resolver: 2 matched, 1 new.
  'murky water'                    -> turbidity  (p=0.93)
  'strong current'                 -> current_speed  (p=0.88)
  'close jacket legs'              -> jacket_leg_spacing  NEW ONTOLOGY ENTRY (p=0.81)
Review every parameter, then set provenance.approved: true on the ones you accept.
```

| `verdy author` option | Default | Description |
| --- | --- | --- |
| `--ontology NAME\|FILE` | `core` | Bundled ontology or a file. `none` turns resolution off: Claude writes every parameter, as in Verdy 0.5. |
| `--resolver` | `exact` | `exact`, `laya`, `llm` or `module:attribute`. |
| `--resolver-option KEY=VALUE` | | Passed to the resolver; repeatable. For Laya: `checkpoint=english\|multilingual`. Other keys go to `laya.Router(...)`. |
| `--min-probability` | `0.5` | A match below this counts as `none`. |
| `--embedder` | `hashing` | `hashing` (local, no dependencies, lexical) or `sentence-transformers[:MODEL]` (local, semantic; `pip install sentence-transformers`). |
| `--top-k` | `15` | Shortlist size. |

The hashing embedder finds shared words and spelling ("strong current" → `current_speed`).
It doesn't find meaning: "murky" resolves to `turbidity` only because "murky water" is a
synonym of that entry. For paraphrases that share no words, use a sentence embedder and
`laya` or `llm`.

## Provenance: why "murky" became `turbidity`

Each parameter records its resolution, and evidence reports embed the ODD, so this record
is part of the sealed evidence:

```yaml
- name: turbidity
  description: Suspended particles in water that reduce optical visibility.
  category: environment
  type: continuous
  unit: NTU
  range: [5.0, 50.0]
  distribution: loguniform
  provenance:
    source: ontology
    confidence: 0.93
    approved: false
    resolution:
      resolver: laya
      model: laya:english
      decision: turbidity
      probability: 0.93
      candidate: water_clarity
      phrase: murky water
      ontology: core@0.1.0
      embedder: hashing
      shortlist:
        - {entry: turbidity, score: 1.0}
        - {entry: current_speed, score: 0.21}
        # ... top 5
```

A miss records `decision: none` and has `source: llm` and `new_ontology_entry: true`. If two
phrases resolve to the same entry, the parameter appears once and lists the second phrase
under `also_mentioned_as`. `metadata.authoring` in the ODD records the ontology, resolver,
embedder and match counts. See the [ODD spec](odd-spec.md#provenance).

## Ontology files

```yaml
name: subsea
version: 0.2.0
entries:
  - name: turbidity
    description: Suspended particles in water that reduce optical visibility.
    category: environment          # environment | platform | task | sensors | faults
    type: continuous               # continuous | temporal | categorical | boolean
    unit: NTU
    range: [0.1, 1000]             # physical bounds; ODDs narrow them
    distribution: loguniform
    synonyms: [murky water, water clarity, silt]
    grounding: {sim: water_turbidity}
```

The schema is `verdy schema ontology`. Names are unique, and `none` is reserved. Numeric
entries need a range, and categorical entries need values. `verdy ontology list [FILE]`
prints the entries. The bundled `core` ontology has 31 entries covering light, weather,
water, terrain, people, platform, sensors and faults.

## Growing the ontology

After review, move the approved new parameters into your ontology:

```console
$ verdy ontology add odd.yaml --ontology subsea.yaml     # updates subsea.yaml in place
$ verdy ontology add odd.yaml --ontology core -o subsea.yaml
Added 1 entries to subsea.yaml: jacket_leg_spacing
```

The command adds only parameters marked `new_ontology_entry` that a human has approved.
It keeps the phrase that mentioned each one as a synonym. The next ODD that mentions
"close jacket legs" then resolves to `jacket_leg_spacing` with no LLM authoring.

## Python API

```python
from verdy.odd.authoring import draft_odd
from verdy.odd.resolve import LayaResolver

odd = draft_odd(description, ontology="subsea.yaml",
                resolver=LayaResolver(min_probability=0.6, checkpoint="english"),
                embedder="sentence-transformers:all-MiniLM-L6-v2", top_k=20)
```

To write a plug-in, implement `resolve(candidate, options, context) -> Resolution` and
return `self._decide(candidate, options, label_or_None, probability, reason)`. Override
`resolve_all` to batch.
