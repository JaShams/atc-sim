from __future__ import annotations

import json
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class LLMAgent:
    """Adapter for text-in/text-out LLM backends.

    The model is expected to return JSON shaped like:
    {"actions": [{"aircraft": "AAL123", "type": "assign_heading", ...}]}
    """

    def __init__(self, client: LLMClient, system_prompt: str | None = None):
        self._client = client
        self._system_prompt = system_prompt or (
            "You are an ATC controller agent. Return JSON only with top-level key 'actions' as a list of action objects."
        )

    def _build_prompt(self, observation: dict[str, Any]) -> str:
        return (
            f"{self._system_prompt}\n"
            "Observation JSON:\n"
            f"{json.dumps(observation, sort_keys=True)}\n"
            "Return strictly valid JSON."
        )

    def act(self, observation: dict) -> dict:
        prompt = self._build_prompt(observation)
        response_text = self._client.complete(prompt)
        try:
            payload = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return {"actions": [response_text]}
        if not isinstance(payload, dict):
            return {"actions": [payload]}
        return payload
