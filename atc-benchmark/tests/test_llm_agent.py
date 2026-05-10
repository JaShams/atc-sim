from __future__ import annotations

from atc_benchmark.agents.base import extract_actions
from atc_benchmark.agents.llm_agent import LLMAgent, MockLLMClient


def _obs() -> dict:
    return {
        "time_sec": 30,
        "decision_points": [{"type": "arrival_on_final", "aircraft": "AAL123"}],
        "snapshot": {
            "aircraft": {"AAL123": {"status": "on_final"}},
            "airport": {"active_runway": "27", "runway_occupied_by": None},
            "weather": {"wind_dir_deg": 260, "wind_speed_kt": 12},
        },
    }


def test_llm_agent_valid_json_response() -> None:
    agent = LLMAgent(client=MockLLMClient('{"actions": [{"aircraft": "AAL123", "type": "clear_to_land"}]}'))
    result = agent.act(_obs())
    actions, malformed = extract_actions(result)
    assert malformed == []
    assert actions == [{"aircraft": "AAL123", "type": "clear_to_land"}]


def test_llm_agent_malformed_json_response() -> None:
    agent = LLMAgent(client=MockLLMClient('not-json'))
    result = agent.act(_obs())
    actions, malformed = extract_actions(result)
    assert actions == []
    assert malformed and malformed[0]["reason"] == "malformed_action"


def test_llm_agent_no_action_response() -> None:
    agent = LLMAgent(client=MockLLMClient('{"actions": []}'))
    result = agent.act(_obs())
    actions, malformed = extract_actions(result)
    assert malformed == []
    assert actions == []


def test_llm_agent_invalid_action_response() -> None:
    agent = LLMAgent(client=MockLLMClient('{"actions": [{"aircraft": "AAL123", "type": "teleport"}]}'))
    result = agent.act(_obs())
    actions, malformed = extract_actions(result)
    assert malformed == []
    assert actions == [{"aircraft": "AAL123", "type": "teleport"}]
