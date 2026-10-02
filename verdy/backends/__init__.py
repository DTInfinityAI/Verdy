"""Execution backends behind a common adapter (built-in sim, log replay, SceneSmith, HIL)."""
from verdy.backends.base import Backend, TraceError, validate_trace
from verdy.backends.function import FunctionBackend
from verdy.backends.replay import ReplayBackend
from verdy.backends.scenesmith import SceneClient, SceneClientBackend, SceneSmithBackend
from verdy.backends.sim2d import HomeNavSim

__all__ = [
    "Backend",
    "FunctionBackend",
    "HomeNavSim",
    "ReplayBackend",
    "SceneClient",
    "SceneClientBackend",
    "SceneSmithBackend",
    "TraceError",
    "validate_trace",
]
