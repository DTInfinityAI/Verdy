"""Run configuration files: which ODD, specs, policy, backend and sampler to evaluate.

A run config is YAML or JSON. Relative paths are resolved against the config file's
directory, and that directory is importable, so ``policy: policy:make_policy`` finds
``policy.py`` next to the config.

.. code-block:: yaml

    odd: odd.yaml
    specs: specs.yaml
    policy:
      factory: policy:make_policy       # module:attribute, called with args
      args: {cruise: 0.8}
    backend:
      type: sim2d                       # sim2d | replay | module:Class
      options: {dt: 0.1, horizon: 20}
    sampler:
      type: importance                  # monte_carlo | stratified | importance | replay
      options: {elite_fraction: 0.2}
    runs: 400
    batch_size: 50
    seed: 7
    verdict:
      max_failure_prob: 0.05
      confidence: 0.95
      min_coverage: 0.8
    output: reports/home_robot.report.json
    credentials:
      fingerprint: sha256               # sha256 | hmac | none

Configs name secrets (e.g. ``secrets: [OPENAI_API_KEY]``) but must never contain them:
a config with a credential-shaped value is rejected before anything runs.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from verdy.backends import Backend, HomeNavSim, ReplayBackend
from verdy.backends.scenesmith import SceneSmithBackend
from verdy.ledger import sha256_file
from verdy.metrics.stl import STLSpec, load_specs
from verdy.odd import ODD, load_odd, read_document
from verdy.sampler import Sampler, make_sampler
from verdy.secrets import FINGERPRINT_MODES, find_credentials
from verdy.verdict import VerdictConfig

_COMPONENT = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "required": ["type"],
            "additionalProperties": False,
            "properties": {"type": {"type": "string"}, "options": {"type": "object"}},
        },
    ]
}

RUN_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["odd", "specs", "policy", "backend"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "odd": {"type": "string"},
        "specs": {"type": "string"},
        "policy": {
            "anyOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "required": ["factory"],
                    "additionalProperties": False,
                    "properties": {"factory": {"type": "string"}, "args": {"type": "object"}},
                },
            ]
        },
        "backend": _COMPONENT,
        "sampler": _COMPONENT,
        "runs": {"type": "integer", "minimum": 1},
        "batch_size": {"type": "integer", "minimum": 1},
        "seed": {"type": "integer"},
        "verdict": {"type": "object"},
        "keep_traces": {"enum": [True, False, "failures"]},
        "output": {"type": "string"},
        "credentials": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"fingerprint": {"enum": list(FINGERPRINT_MODES)}},
        },
    },
}

BUILTIN_BACKENDS: dict[str, type[Backend]] = {
    "sim2d": HomeNavSim,
    "replay": ReplayBackend,
    "scenesmith": SceneSmithBackend,
}
# Backend options that are paths, resolved against the config file's directory.
_PATH_OPTIONS = ("cache_dir", "scenesmith_dir")


class ConfigError(ValueError):
    """Raised for invalid run configs."""


@dataclass
class RunConfig:
    path: Path
    raw: dict[str, Any]
    odd: ODD
    specs: list[STLSpec]
    policy: Any
    backend: Backend
    sampler: Sampler
    runs: int
    batch_size: int | None
    verdict: VerdictConfig
    keep_traces: bool | str
    output: Path
    fingerprint: str = "sha256"
    file_hashes: dict[str, str] = field(default_factory=dict)


def _component(spec: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(spec, str):
        return spec, {}
    return spec["type"], dict(spec.get("options") or {})


def import_object(ref: str, base_dir: Path) -> Any:
    """Import ``module:attr`` or ``path/to/file.py:attr`` (relative to ``base_dir``)."""
    if ":" not in ref:
        raise ConfigError(f"expected 'module:attribute', got {ref!r}")
    module_ref, attr = ref.rsplit(":", 1)
    local = base_dir / (module_ref.replace(".", "/") + ".py")
    if module_ref.endswith(".py") or local.is_file():
        # Load files next to the config by path, under a unique name, so two configs with
        # a "policy.py" each never share a cached module.
        path = (base_dir / module_ref).resolve() if module_ref.endswith(".py") else local.resolve()
        if str(path.parent) not in sys.path:  # let the module import its siblings
            sys.path.insert(0, str(path.parent))
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        name = f"verdy_user_{path.stem}_{digest}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ConfigError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    else:
        if str(base_dir) not in sys.path:
            sys.path.insert(0, str(base_dir))
        module = importlib.import_module(module_ref)
    obj: Any = module
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise ConfigError(f"{module_ref} has no attribute {attr!r}") from None
    return obj


def load_run_config(
    path: str | Path, *, runs: int | None = None, seed: int | None = None
) -> RunConfig:
    """Load a run config and construct every component it names."""
    path = Path(path).resolve()
    base = path.parent
    raw = read_document(path)
    leaked = find_credentials(raw)
    if leaked:
        raise ConfigError(
            f"{path.name} contains what looks like a credential at: {', '.join(leaked)}. "
            "Never put API keys in configs: set them as environment variables or in the "
            "Verdy secrets file, and refer to them by name."
        )
    try:
        jsonschema.validate(raw, RUN_CONFIG_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ConfigError(f"{path.name}: {where}: {exc.message}") from exc

    odd_path, specs_path = base / raw["odd"], base / raw["specs"]
    odd = load_odd(odd_path)
    specs = load_specs(specs_path)

    pol = raw["policy"]
    if isinstance(pol, str):
        policy = import_object(pol, base)
        if isinstance(policy, type):
            policy = policy()
    else:
        policy = import_object(pol["factory"], base)(**(pol.get("args") or {}))

    kind, options = _component(raw["backend"])
    for key in _PATH_OPTIONS:
        if isinstance(options.get(key), str) and not options[key].startswith("~"):
            options[key] = str(base / options[key])
    prompt = options.get("prompt")
    if isinstance(prompt, dict) and isinstance(prompt.get("cache_dir"), str):
        prompt["cache_dir"] = str(base / prompt["cache_dir"])
    if kind in BUILTIN_BACKENDS:
        backend = BUILTIN_BACKENDS[kind](**options)
    else:
        backend = import_object(kind, base)(**options)
    if not isinstance(backend, Backend):
        raise ConfigError(f"backend {kind!r} does not implement verdy.backends.Backend")

    run_seed = seed if seed is not None else int(raw.get("seed", 0))
    s_kind, s_options = _component(raw.get("sampler", "monte_carlo"))
    if "logs" in s_options:
        s_options["logs"] = str(base / s_options["logs"])
    sampler = make_sampler(s_kind, odd, seed=run_seed, **s_options)

    n = runs if runs is not None else int(raw.get("runs", 100))
    if s_kind == "replay" and runs is None and "runs" not in raw:
        n = len(sampler)  # type: ignore[arg-type]

    out = raw.get("output") or f"reports/{raw.get('name', path.stem)}.report.json"
    return RunConfig(
        path=path,
        raw=raw,
        odd=odd,
        specs=specs,
        policy=policy,
        backend=backend,
        sampler=sampler,
        runs=n,
        batch_size=raw.get("batch_size"),
        verdict=VerdictConfig.from_dict(raw.get("verdict") or {}),
        keep_traces=raw.get("keep_traces", False),
        output=(base / out),
        fingerprint=(raw.get("credentials") or {}).get("fingerprint", "sha256"),
        file_hashes={
            "config": sha256_file(path),
            "odd_file": sha256_file(odd_path),
            "specs_file": sha256_file(specs_path),
        },
    )
