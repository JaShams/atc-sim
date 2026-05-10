from __future__ import annotations

from typing import Protocol


class Agent(Protocol):
    def act(self, observation: dict) -> dict: ...
