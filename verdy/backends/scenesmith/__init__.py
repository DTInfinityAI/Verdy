"""SceneSmith integration.

* :class:`SceneSmithBackend`: generate a scene per scenario with SceneSmith, run the
  policy, and validate the task with SceneSmith's validator.
* :class:`SceneClientBackend`: step a simulator that loads SceneSmith scenes yourself and
  record signals every control step.
"""
from verdy.backends.scenesmith.client import SceneClient, SceneClientBackend
from verdy.backends.scenesmith.pipeline import (
    PolicyOutcome,
    SceneSmithBackend,
    SceneSmithError,
    SceneSmithPolicy,
    SceneSmithScene,
)
from verdy.backends.scenesmith.prompts import (
    ClaudePromptWriter,
    PromptWriter,
    TemplatePromptWriter,
    make_prompt_writer,
)

__all__ = [
    "ClaudePromptWriter",
    "PolicyOutcome",
    "PromptWriter",
    "SceneClient",
    "SceneClientBackend",
    "SceneSmithBackend",
    "SceneSmithError",
    "SceneSmithPolicy",
    "SceneSmithScene",
    "TemplatePromptWriter",
    "make_prompt_writer",
]
