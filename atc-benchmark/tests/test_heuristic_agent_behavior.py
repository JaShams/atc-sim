from atc_benchmark.agents.heuristic_agent import HeuristicAgent


def _snapshot():
    return {
        "airport": {"runway_id": "09", "active_runway": "09", "runway_occupied_by": None},
        "aircraft": {
            "ARR1": {
                "callsign": "ARR1",
                "role": "arrival",
                "status": "airborne",
                "x_nm": -8,
                "y_nm": 0,
                "altitude_ft": 1200,
                "speed_kt": 180,
                "heading_deg": 90,
                "target_runway": "09",
                "emergency": True,
            },
            "DEP1": {
                "callsign": "DEP1",
                "role": "departure",
                "status": "waiting_departure",
                "x_nm": 0,
                "y_nm": 0,
                "altitude_ft": 0,
                "speed_kt": 0,
                "heading_deg": 90,
                "target_runway": "09",
                "emergency": False,
            },
        },
    }


def test_heuristic_is_deterministic_for_same_observation():
    obs = {
        "time_sec": 0,
        "snapshot": _snapshot(),
        "decision_points": [
            {"type": "departure_ready", "aircraft": ["DEP1"]},
            {"type": "emergency", "aircraft": ["ARR1"]},
        ],
    }
    agent = HeuristicAgent()
    assert agent.act(obs) == agent.act(obs)


def test_heuristic_prioritizes_clearable_emergency_landing():
    obs = {
        "time_sec": 0,
        "snapshot": _snapshot(),
        "decision_points": [
            {"type": "departure_ready", "aircraft": ["DEP1"]},
            {"type": "emergency", "aircraft": ["ARR1"]},
        ],
    }
    actions = HeuristicAgent().act(obs)["actions"]
    assert actions[0] == {"aircraft": "ARR1", "type": "clear_to_land"}
    assert all(action["type"] != "clear_for_takeoff" for action in actions)


def test_heuristic_holds_clearances_during_wind_mismatch():
    obs = {
        "time_sec": 0,
        "snapshot": _snapshot(),
        "decision_points": [
            {"type": "wind_runway_mismatch", "aircraft": ["ARR1", "DEP1"]},
            {"type": "departure_ready", "aircraft": ["DEP1"]},
        ],
    }
    actions = HeuristicAgent().act(obs)["actions"]
    assert all(action["type"] not in {"clear_to_land", "clear_for_takeoff"} for action in actions)


def test_heuristic_conflict_heading_uses_geometry():
    obs = {
        "time_sec": 0,
        "snapshot": _snapshot(),
        "decision_points": [{"type": "active_conflict", "aircraft": ["ARR1", "DEP1"]}],
    }
    action = HeuristicAgent().act(obs)["actions"][0]
    assert action["type"] == "assign_heading"
    assert action["heading"] != 45
