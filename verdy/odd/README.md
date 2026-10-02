# odd

ODD parsing, validation, and LLM-assisted authoring. Format: [`docs/odd-spec.md`](../../docs/odd-spec.md).

| Module | Contents |
| --- | --- |
| `model.py` | `ODD` and `Parameter` dataclasses |
| `validation.py` | `check_odd` (errors and warnings) and `validate_odd` (raises) |
| `loader.py` | `load_odd`, `save_odd`, `read_document` for YAML and JSON |
| `constraints.py` | Safe evaluation of constraint expressions |
| `authoring.py` | `draft_odd`: draft an ODD from a description with Claude |
