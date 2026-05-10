from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class HTTPLLMClient:
    """Provider-agnostic HTTP JSON client for chat/completions-style APIs."""

    def __init__(self, endpoint: str, model: str, api_key: str | None = None, timeout_sec: float = 20.0):
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._timeout_sec = timeout_sec

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "format": "json",
        }
        req = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:  # noqa: S310
                raw = json.loads(resp.read().decode("utf-8"))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            return ""

        if isinstance(raw, dict):
            for key in ("response", "text", "content"):
                value = raw.get(key)
                if isinstance(value, str):
                    return value
            message = raw.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message")
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        return msg["content"]
                    if isinstance(first.get("text"), str):
                        return first["text"]
        return ""


class MockLLMClient:
    def __init__(self, response: str):
        self._response = response

    def complete(self, prompt: str) -> str:
        _ = prompt
        return self._response


class LLMAgent:
    """Adapter for text-in/text-out LLM backends."""

    def __init__(self, client: LLMClient, system_prompt: str | None = None):
        self._client = client
        self._system_prompt = system_prompt or (
            "You are an ATC controller agent. Return ONLY strict JSON with top-level key 'actions' as a list of action objects."
        )

    @classmethod
    def from_env(cls) -> "LLMAgent":
        mock = os.getenv("ATC_LLM_MOCK_RESPONSE")
        if mock is not None:
            return cls(client=MockLLMClient(mock))

        provider = os.getenv("ATC_LLM_PROVIDER", "ollama").lower()
        model = os.getenv("ATC_LLM_MODEL", "llama3")
        endpoint = os.getenv("ATC_LLM_ENDPOINT")
        if not endpoint:
            endpoint = "http://localhost:11434/api/generate" if provider == "ollama" else "http://localhost:11434/api/generate"
        api_key = os.getenv("ATC_LLM_API_KEY")
        timeout = float(os.getenv("ATC_LLM_TIMEOUT_SEC", "20"))
        return cls(client=HTTPLLMClient(endpoint=endpoint, model=model, api_key=api_key, timeout_sec=timeout))

    def _build_prompt(self, observation: dict[str, Any]) -> str:
        snapshot = observation.get("snapshot", {}) if isinstance(observation, dict) else {}
        return (
            f"{self._system_prompt}\n"
            "Inputs:\n"
            f"- current_time_sec: {observation.get('time_sec')}\n"
            f"- decision_points: {json.dumps(observation.get('decision_points', []), sort_keys=True)}\n"
            f"- aircraft_state: {json.dumps(snapshot.get('aircraft', {}), sort_keys=True)}\n"
            f"- runway_state: {json.dumps(snapshot.get('airport', {}), sort_keys=True)}\n"
            f"- weather: {json.dumps(snapshot.get('weather', {}), sort_keys=True)}\n"
            "Allowed action schema:\n"
            "{\"actions\": [{\"aircraft\": str, \"type\": one_of(assign_heading, assign_altitude, assign_speed, clear_to_land, clear_for_takeoff, go_around, hold_short, hold_position, no_op), \"heading\"?: number, \"altitude_ft\"?: number, \"speed_kt\"?: number}]}\n"
            "Return ONLY JSON. Do not include markdown, prose, or code fences."
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
        return {"actions": payload.get("actions", [])}
