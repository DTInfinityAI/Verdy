"""Scenarios taken from recorded logs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from verdy.odd.model import ODD
from verdy.sampler.base import Sampler, Scenario


class LogReplaySampler(Sampler):
    """Yields one scenario per recorded log file, in sorted file order.

    Each log is a JSON file with the ODD parameter values it was recorded under in
    ``"params"`` and the recorded signals in ``"trace"`` (see
    :class:`verdy.backends.replay.ReplayBackend`). The scenario's
    ``metadata["log_path"]`` tells the replay backend which file to load.
    """

    def __init__(self, odd: ODD, logs: str | Path, seed: int = 0, **kwargs: Any) -> None:
        super().__init__(odd, seed=seed, **kwargs)
        self.logs_dir = Path(logs)
        self.paths = sorted(self.logs_dir.glob("*.json"))
        if not self.paths:
            raise FileNotFoundError(f"no *.json logs found in {self.logs_dir}")
        self._next = 0

    def __len__(self) -> int:
        return len(self.paths)

    def config(self) -> dict[str, Any]:
        return {**super().config(), "logs": str(self.logs_dir), "n_logs": len(self.paths)}

    def sample(self, n: int) -> list[Scenario]:
        out = []
        for path in self.paths[self._next : self._next + n]:
            data = json.loads(path.read_text("utf-8"))
            params = {k: v for k, v in data.get("params", {}).items() if k in self.odd}
            out.append(self._make(params, log_path=str(path)))
        self._next += len(out)
        return out
