"""SceneSmith integration: generate a scene per scenario, run the policy, validate the task.

For every sampled scenario the backend:

1. **Writes a scene prompt** from the scenario's parameters (template or Claude, see
   :mod:`.prompts`).
2. **Generates the scene with SceneSmith**, by running SceneSmith's ``main.py`` with a
   one-row prompts CSV in its own checkout and Python environment. Scenes are cached by a
   hash of the prompt and generation settings, so a scene is only generated once.
3. **Runs the policy** on the scene. Following SceneSmith's robot-evaluation contract,
   the policy receives the initial ``.dmd.yaml`` scene and writes the final scene (with
   the poses the robot left objects in) to the path it is given.
4. **Validates the task** with SceneSmith's validator agent and turns the result into
   trace signals: ``task_score`` (0-1), ``task_success`` (1 or 0) and
   ``requirements_met`` (share of requirements scored 1.0).

SceneSmith's agents call OpenAI, so generation and validation need ``OPENAI_API_KEY``
(plus ``GOOGLE_API_KEY`` if you use its Gemini image backend). Keys are taken from
:mod:`verdy.secrets` and passed only to the SceneSmith subprocesses, in an otherwise
minimal environment. All subprocess output is redacted before it is logged or stored.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from verdy.backends.base import Backend
from verdy.backends.scenesmith.prompts import PromptWriter, make_prompt_writer
from verdy.secrets import find_credentials, get_secret, redact, subprocess_env

HELPER = Path(__file__).with_name("_validate_task.py")
SUCCESS_THRESHOLD = 0.9  # SceneSmith counts a task as successful at overall score >= 0.9


class SceneSmithError(RuntimeError):
    """Raised when SceneSmith generation or validation fails."""


@dataclass
class SceneSmithScene:
    """A generated scene handed to the policy."""

    scenario_id: str
    prompt: str
    task: str
    scene_dir: Path
    """Scene root (``scene_000``); register it as the ``scene`` package for Drake."""
    dmd: Path
    """Initial scene as Drake model directives (``house.dmd.yaml`` or ``scene.dmd.yaml``)."""
    state: Path
    """Scene metadata (``house_state.json`` or ``scene_state.json``)."""
    cache_key: str
    cached: bool
    work_dir: Path
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyOutcome:
    """What a SceneSmith policy may return instead of a bare path.

    ``trace`` holds signals the policy recorded while acting (e.g. from a Drake
    simulation), so STL specs can combine them with the task validation signals.
    """

    dmd: Path | None = None
    trace: dict[str, list[float]] | None = None
    info: dict[str, Any] = field(default_factory=dict)


class SceneSmithPolicy(Protocol):
    def run(self, scene: SceneSmithScene, output_dmd: Path, seed: int) -> Any: ...


class SceneSmithBackend(Backend):
    """Evaluate a policy on SceneSmith-generated scenes.

    Args:
        task: the robot task, e.g. ``"Find a fruit and place it on the kitchen table"``.
        scenesmith_dir: SceneSmith checkout (default: ``$SCENESMITH_DIR``).
        python: SceneSmith's Python interpreter (default: ``<scenesmith_dir>/.venv/bin/python``).
        prompt: prompt writer config, e.g. ``{"writer": "claude"}`` or
            ``{"writer": "template", "template": "A {floor_type} kitchen ..."}``.
        cache_dir: where scenes, prompts and per-run files are stored.
        generation_overrides: extra Hydra overrides for SceneSmith's ``main.py``,
            e.g. ``["experiment.pipeline.stop_stage=furniture"]``.
        secrets: secrets SceneSmith needs (names only); checked before any work starts.
        optional_secrets: secrets passed to SceneSmith only when set.
        extra_env: non-secret environment variables for SceneSmith, e.g.
            ``{"WANDB_MODE": "disabled"}``.
        vision: let the validator render the scene (needs Blender, as in SceneSmith).
        validator_model: OpenAI model for SceneSmith's validator (its default if unset).
        generation_timeout, validation_timeout: seconds.
    """

    name = "scenesmith"

    def __init__(
        self,
        task: str,
        scenesmith_dir: str | Path | None = None,
        python: str | Path | None = None,
        prompt: dict[str, Any] | PromptWriter | None = None,
        cache_dir: str | Path = ".verdy/scenesmith",
        generation_overrides: list[str] | None = None,
        secrets: list[str] | None = None,
        optional_secrets: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        vision: bool = True,
        validator_model: str | None = None,
        generation_timeout: float = 4 * 3600,
        validation_timeout: float = 1800,
    ) -> None:
        root = scenesmith_dir or os.environ.get("SCENESMITH_DIR")
        if not root:
            raise ValueError("set scenesmith_dir in the backend options or $SCENESMITH_DIR")
        self.scenesmith_dir = Path(root).expanduser().resolve()
        if not (self.scenesmith_dir / "main.py").is_file():
            raise ValueError(f"{self.scenesmith_dir} is not a SceneSmith checkout (no main.py)")
        if python is None:
            python = self.scenesmith_dir / ".venv" / "bin" / "python"
            if not python.exists():
                raise ValueError(
                    f"no SceneSmith environment at {python}: run `uv sync` in "
                    f"{self.scenesmith_dir}, or set the backend's 'python' option"
                )
        self.python = str(python)
        self.task = task
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        if isinstance(prompt, PromptWriter):
            self.prompt_writer = prompt
        else:
            spec = dict(prompt or {"writer": "claude"})
            if spec.get("writer", "claude") == "claude" and "cache_dir" not in spec:
                spec["cache_dir"] = str(self.cache_dir / "prompts")
            self.prompt_writer = make_prompt_writer(spec)
        self.generation_overrides = list(generation_overrides or [])
        self.secrets = list(secrets if secrets is not None else ["OPENAI_API_KEY"])
        self.optional_secrets = list(
            optional_secrets if optional_secrets is not None else ["GOOGLE_API_KEY"])
        self.extra_env = dict(extra_env or {})
        leaked = find_credentials(self.extra_env)
        if leaked:
            raise ValueError(
                "extra_env must not contain credentials (found in: " + ", ".join(leaked)
                + "); list the secret's name under 'secrets' instead"
            )
        self.vision = vision
        self.validator_model = validator_model
        self.generation_timeout = generation_timeout
        self.validation_timeout = validation_timeout
        self._version = self._scenesmith_version()

    # -- reporting ------------------------------------------------------------------

    def secret_names(self) -> list[str]:
        return sorted(set(self.secrets) | set(self.optional_secrets)
                      | set(self.prompt_writer.secret_names))

    def bind(self, odd: Any) -> None:
        super().bind(odd)
        for name in self.secrets:
            get_secret(name)  # fail fast, before generating anything

    def config(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "scenesmith_version": self._version,
            "prompt_writer": self.prompt_writer.config(),
            "generation_overrides": self.generation_overrides,
            "vision": self.vision,
            "validator_model": self.validator_model,
        }

    def describe(self, env: object) -> dict[str, Any]:
        assert isinstance(env, SceneSmithScene)
        return {"prompt": env.prompt, "scene_key": env.cache_key, "cached": env.cached,
                **env.info}

    def _scenesmith_version(self) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.scenesmith_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10, check=True,
            )
            return out.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    # -- subprocesses ---------------------------------------------------------------

    def _run(self, cmd: list[str], timeout: float, log_file: Path) -> str:
        env = subprocess_env(allow=self.optional_secrets, required=self.secrets,
                             extra=self.extra_env)
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, cwd=self.scenesmith_dir, env=env, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise SceneSmithError(f"{Path(cmd[1]).name} timed out after {timeout:.0f} s") from None
        output = redact((proc.stdout or "") + (proc.stderr or ""))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output, "utf-8")
        if proc.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-15:])
            raise SceneSmithError(
                f"{Path(cmd[1]).name} exited with {proc.returncode} after "
                f"{time.perf_counter() - start:.0f} s (log: {log_file}):\n{tail}"
            )
        return output

    # -- Backend --------------------------------------------------------------------

    def build(self, scenario: dict) -> SceneSmithScene:
        prompt = self.prompt_writer.write(self.sim_params(scenario), self.odd, self.task)
        key = hashlib.sha256(json.dumps(
            {"prompt": prompt, "overrides": self.generation_overrides,
             "scenesmith": self._version}, sort_keys=True).encode()).hexdigest()[:24]
        out_dir = self.cache_dir / "scenes" / key
        cached = (out_dir / "scene_000").is_dir()
        if not cached:
            if out_dir.exists():
                shutil.rmtree(out_dir)  # a previous attempt that did not finish
            out_dir.mkdir(parents=True)
            csv_path = out_dir / "prompts.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["scene_index", "prompt"])
                writer.writerow([0, prompt])
            self._run(
                [self.python, "main.py", f"+name=verdy_{key[:12]}",
                 f"experiment.csv_path={csv_path}", f"hydra.run.dir={out_dir}",
                 *self.generation_overrides],
                self.generation_timeout, out_dir / "generation.log",
            )
            (out_dir / "verdy_prompt.json").write_text(
                json.dumps({"prompt": prompt, "task": self.task}, indent=2), "utf-8")
        scene_dir = out_dir / "scene_000"
        dmd, state = _find_scene_files(scene_dir)
        work_dir = self.cache_dir / "runs" / scenario["id"]
        work_dir.mkdir(parents=True, exist_ok=True)
        return SceneSmithScene(
            scenario_id=scenario["id"], prompt=prompt, task=self.task, scene_dir=scene_dir,
            dmd=dmd, state=state, cache_key=key, cached=cached, work_dir=work_dir,
        )

    def rollout(self, env: object, policy: object, seed: int) -> dict:
        assert isinstance(env, SceneSmithScene)
        output_dmd = env.work_dir / f"final.{env.dmd.name}"
        start = time.perf_counter()
        outcome = _call_policy(policy, env, output_dmd, seed)
        env.info["policy_seconds"] = round(time.perf_counter() - start, 3)
        final_dmd = outcome.dmd or output_dmd
        if not final_dmd.is_file():
            raise SceneSmithError(f"policy did not write the final scene to {final_dmd}")
        env.info.update(outcome.info)

        cmd = [self.python, str(HELPER), "--scene-state", str(env.state), "--dmd",
               str(final_dmd), "--scene-dir", str(env.scene_dir), "--task", self.task]
        if not self.vision:
            cmd.append("--no-vision")
        if self.validator_model:
            cmd += ["--model", self.validator_model]
        output = self._run(cmd, self.validation_timeout, env.work_dir / "validation.log")
        result = _parse_result(output)
        env.info["validation"] = result

        score = result["overall_score"]
        success = 1.0 if result["overall_success"] else 0.0
        reqs = result["requirements"]
        met = sum(r["score"] >= 1.0 for r in reqs) / len(reqs) if reqs else success
        trace = dict(outcome.trace) if outcome.trace else {"time": [0.0, 1.0]}
        n = len(trace["time"])
        trace.update(task_score=[score] * n, task_success=[success] * n,
                     requirements_met=[met] * n)
        return trace


def _find_scene_files(scene_dir: Path) -> tuple[Path, Path]:
    """The final scene's directives and state, preferring the combined house."""
    combined = scene_dir / "combined_house"
    if (combined / "house.dmd.yaml").is_file() and (combined / "house_state.json").is_file():
        return combined / "house.dmd.yaml", combined / "house_state.json"
    for dmd in sorted(scene_dir.rglob("*.dmd.yaml")):
        states = sorted(dmd.parent.glob("*state.json"))
        if states:
            return dmd, states[0]
    raise SceneSmithError(f"no .dmd.yaml scene with a *state.json found under {scene_dir}")


def _call_policy(policy: Any, scene: SceneSmithScene, output_dmd: Path, seed: int) -> PolicyOutcome:
    fn: Callable[..., Any] | None = getattr(policy, "run", None)
    if fn is None and callable(policy):
        fn = policy
    if fn is None:
        raise TypeError("a SceneSmith policy needs run(scene, output_dmd, seed) or __call__")
    result = fn(scene, output_dmd, seed)
    if isinstance(result, PolicyOutcome):
        return result
    if isinstance(result, (str, Path)):
        return PolicyOutcome(dmd=Path(result))
    return PolicyOutcome()


def _parse_result(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if line.startswith("VERDY_RESULT "):
            return json.loads(line[len("VERDY_RESULT "):])
    raise SceneSmithError("SceneSmith validator produced no result")


__all__ = [
    "PolicyOutcome",
    "SceneSmithBackend",
    "SceneSmithError",
    "SceneSmithPolicy",
    "SceneSmithScene",
]
