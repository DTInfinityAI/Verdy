"""Turn sampled ODD scenarios into SceneSmith scene prompts.

SceneSmith builds a scene from a natural-language description. A prompt writer turns one
scenario's parameter values into that description:

* :class:`TemplatePromptWriter` fills a format string. Deterministic, no API calls.
* :class:`ClaudePromptWriter` asks Claude to write a vivid, specific prompt that encodes
  every parameter value. Prompts are cached on disk by a hash of their inputs, so reruns
  reuse them (reproducible, and no repeat API cost), and each prompt is recorded in the
  evidence report.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from verdy.llm import API_KEY_SECRET, DEFAULT_MODEL, claude_client, create_json
from verdy.odd.model import ODD


class PromptWriter:
    """Base class: ``write(params, odd, task) -> str``."""

    secret_names: tuple[str, ...] = ()

    def write(self, params: dict[str, Any], odd: ODD | None, task: str) -> str:
        raise NotImplementedError

    def config(self) -> dict[str, Any]:
        return {"writer": type(self).__name__}


class TemplatePromptWriter(PromptWriter):
    """Fill ``template`` with scenario parameters, e.g.
    ``"A {floor_type} kitchen lit at {lighting:.0f} lux with {clutter} clutter."``
    The task is available as ``{task}``.
    """

    def __init__(self, template: str) -> None:
        self.template = template

    def write(self, params: dict[str, Any], odd: ODD | None, task: str) -> str:
        try:
            return self.template.format(task=task, **params)
        except KeyError as exc:
            raise ValueError(f"prompt template uses unknown parameter {exc.args[0]!r}") from None

    def config(self) -> dict[str, Any]:
        return {**super().config(), "template": self.template}


SYSTEM_PROMPT = """\
You write scene descriptions for SceneSmith, which generates simulation-ready indoor 3D \
scenes for robot testing from a text prompt.

You get a robot task and the values of the test parameters for one test scenario. Write \
one prompt (2-5 sentences) describing the room or house the robot will operate in. Every \
parameter value that affects the physical scene (room type and size, furniture, objects, \
clutter, materials, lighting, obstacles, object placement) must be stated concretely, so \
the generated scene reflects this exact scenario. Name the objects the task needs and \
where they are. Describe only the scene: no robot, no instructions, no parameter names. \
Do not add hazards or objects the parameters do not imply."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
    "additionalProperties": False,
}
PROMPT_VERSION = "1"


class ClaudePromptWriter(PromptWriter):
    """Write scene prompts with Claude.

    Args:
        cache_dir: where prompts are cached; ``None`` disables caching.
        model, effort: Claude model and effort level.
        api_key_secret: name of the secret holding the Anthropic API key.
        client: an ``anthropic.Anthropic`` client (created from the secret if omitted).
    """

    def __init__(
        self,
        cache_dir: str | Path | None = ".verdy/prompts",
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        api_key_secret: str = API_KEY_SECRET,
        client: Any = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.model = model
        self.effort = effort
        self.api_key_secret = api_key_secret
        self.secret_names = (api_key_secret,)
        self._client = client

    def config(self) -> dict[str, Any]:
        return {**super().config(), "model": self.model, "effort": self.effort,
                "prompt_version": PROMPT_VERSION}

    def _describe(self, params: dict[str, Any], odd: ODD | None) -> list[dict[str, Any]]:
        rows = []
        for name, value in params.items():
            row: dict[str, Any] = {"parameter": name, "value": value}
            if odd is not None and name in odd:
                p = odd[name]
                if p.description:
                    row["meaning"] = p.description
                if p.unit:
                    row["unit"] = p.unit
                if p.range:
                    row["range"] = list(p.range)
            rows.append(row)
        return rows

    def write(self, params: dict[str, Any], odd: ODD | None, task: str) -> str:
        request = {
            "task": task,
            "parameters": self._describe(params, odd),
            "model": self.model,
            "effort": self.effort,
            "prompt_version": PROMPT_VERSION,
        }
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache_file and cache_file.is_file():
            return json.loads(cache_file.read_text("utf-8"))["prompt"]

        if self._client is None:
            self._client = claude_client(self.api_key_secret)
        user = (
            f"Robot task: {task}\n\nScenario parameters:\n"
            + json.dumps(request["parameters"], indent=2)
        )
        result, _ = create_json(
            self._client, system=SYSTEM_PROMPT, schema=OUTPUT_SCHEMA,
            messages=[{"role": "user", "content": user}],
            model=self.model, effort=self.effort, max_tokens=4000,
        )
        prompt = result["prompt"].strip()
        if not prompt:
            raise ValueError("Claude returned an empty scene prompt")
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"prompt": prompt, "request": request}), "utf-8")
        return prompt


def make_prompt_writer(spec: dict[str, Any] | None, base_dir: Path | None = None) -> PromptWriter:
    """Build a writer from config: ``{writer: template, template: ...}`` or
    ``{writer: claude, model: ..., effort: ..., cache_dir: ...}``."""
    spec = dict(spec or {"writer": "claude"})
    kind = spec.pop("writer", "claude")
    if kind == "template":
        return TemplatePromptWriter(**spec)
    if kind == "claude":
        if base_dir is not None and spec.get("cache_dir"):
            spec["cache_dir"] = base_dir / spec["cache_dir"]
        return ClaudePromptWriter(**spec)
    raise ValueError(f"unknown prompt writer {kind!r}; use 'template' or 'claude'")
