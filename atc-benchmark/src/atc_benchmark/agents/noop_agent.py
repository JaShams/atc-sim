from __future__ import annotations


class NoOpAgent:
    def act(self, _observation: dict) -> dict:
        return {"actions": []}
