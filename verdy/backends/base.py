"""Common adapter every execution backend implements."""
from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def build(self, scenario: dict) -> object:
        """Realize a sampled scenario as a runnable environment."""

    @abstractmethod
    def rollout(self, env: object, policy: object, seed: int) -> dict:
        """Run the policy and return a time-stamped trace of signals."""
