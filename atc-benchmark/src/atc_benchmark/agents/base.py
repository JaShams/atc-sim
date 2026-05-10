from __future__ import annotations

from typing import Protocol


class Agent(Protocol):
    def act(self, observation: dict) -> dict: ...


def extract_actions(agent_result: object) -> tuple[list[dict], list[dict]]:
    """Normalize AgentResult to a list of action dicts.

    Returns (actions, invalid_action_records).
    """
    if not isinstance(agent_result, dict):
        return [], [{"action": agent_result, "reason": "malformed_agent_result"}]
    actions = agent_result.get("actions", [])
    if not isinstance(actions, list):
        return [], [{"action": actions, "reason": "malformed_actions_list"}]
    normalized: list[dict] = []
    invalid: list[dict] = []
    for action in actions:
        if isinstance(action, dict):
            normalized.append(action)
        else:
            invalid.append({"action": action, "reason": "malformed_action"})
    return normalized, invalid
