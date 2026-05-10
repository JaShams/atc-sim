from pathlib import Path

from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.simulator.engine import load_world, run
from atc_benchmark.simulator.models import Aircraft, AirportState, RulesConfig, Weather, WorldState


class ScriptedAgent:
    def __init__(self, actions_per_tick):
        self.actions_per_tick = actions_per_tick
        self.i = 0

    def act(self, _obs):
        actions = self.actions_per_tick[self.i] if self.i < len(self.actions_per_tick) else []
        self.i += 1
        return {"actions": actions}


class EmergencyLandingAgent:
    def act(self, observation):
        actions = []
        for dp in observation["decision_points"]:
            for callsign in dp.get("aircraft", []):
                if callsign == "ARR_EMG":
                    actions.append({"aircraft": "ARR_EMG", "type": "clear_to_land"})
        return {"actions": actions}


def test_run_returns_score(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    result = run(world, HeuristicAgent(), max_ticks=5, trace_path=tmp_path / "trace.jsonl")
    assert "score" in result
    assert "metrics" in result
    assert (tmp_path / "trace.jsonl").exists()


def _world_for_predicted_conflict_tests() -> WorldState:
    return WorldState(
        time_sec=0,
        tick_sec=5,
        airport=AirportState(runway_id="27", active_runway="27"),
        weather=Weather(),
        rules=RulesConfig(lookahead_seconds=60, min_horizontal_nm=3.0, min_vertical_ft=1000.0),
        aircraft={
            "A1": Aircraft(callsign="A1", role="arrival", x_nm=0.0, y_nm=-5.0, altitude_ft=5000, speed_kt=240, heading_deg=0, status="airborne"),
            "A2": Aircraft(callsign="A2", role="arrival", x_nm=0.0, y_nm=5.0, altitude_ft=5000, speed_kt=240, heading_deg=180, status="airborne"),
            "A3": Aircraft(callsign="A3", role="arrival", x_nm=50.0, y_nm=50.0, altitude_ft=5000, speed_kt=240, heading_deg=90, status="airborne"),
        },
    )


def test_invalid_commands_reduce_score(tmp_path):
    world = _world_for_predicted_conflict_tests()
    result = run(world, ScriptedAgent([[{"aircraft": "A1", "type": "not_a_real_action"}]]), max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["score_breakdown"]["invalid_command"] < 0


def test_secondary_conflicts_reduce_score(tmp_path):
    world = _world_for_predicted_conflict_tests()
    world.aircraft["A3"].x_nm = 6.0
    world.aircraft["A3"].y_nm = -5.0
    world.aircraft["A3"].heading_deg = 270
    result = run(world, ScriptedAgent([[{"aircraft": "A1", "type": "assign_heading", "heading": 90}]]), max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflict_introduced_count"] >= 2
    assert result["score_breakdown"]["secondary_conflicts_created"] == 0


def test_resolving_conflict_improves_score(tmp_path):
    world = _world_for_predicted_conflict_tests()
    result = run(world, ScriptedAgent([[{"aircraft": "A1", "type": "assign_heading", "heading": 90}]]), max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["score_breakdown"]["conflict_resolved"] > 0


def test_delay_reduces_score(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    world.aircraft["ARR1"].ideal_landing_time_sec = 0
    world.aircraft["DEP1"].ideal_takeoff_time_sec = 0
    result = run(world, ScriptedAgent([]), max_ticks=2, trace_path=tmp_path / "trace.jsonl")
    assert result["score_breakdown"]["arrival_delay_sec"] <= 0
    assert result["score_breakdown"]["departure_delay_sec"] <= 0


def test_emergency_handling_affects_score(tmp_path):
    handled_world = load_world(Path("scenarios/emergency_priority_landing_001.json"))
    handled_world.aircraft["ARR_EMG"].emergency = True
    handled_world.aircraft["ARR_EMG"].status = "on_final"
    handled_world.aircraft["ARR_EMG"].x_nm = 0.0
    handled_world.aircraft["ARR_EMG"].y_nm = 0.0
    handled_world.aircraft["ARR_EMG"].altitude_ft = 200
    handled = run(handled_world, EmergencyLandingAgent(), max_ticks=2, trace_path=tmp_path / "handled_trace.jsonl")

    unhandled_world = load_world(Path("scenarios/emergency_priority_landing_001.json"))
    unhandled_world.aircraft["ARR_EMG"].emergency = True
    unhandled = run(unhandled_world, ScriptedAgent([]), max_ticks=2, trace_path=tmp_path / "unhandled_trace.jsonl")

    handled_total = handled["score_breakdown"]["emergency_handled"] + handled["score_breakdown"]["emergency_unhandled"]
    unhandled_total = unhandled["score_breakdown"]["emergency_handled"] + unhandled["score_breakdown"]["emergency_unhandled"]
    assert handled_total > unhandled_total


def test_conflict_lifecycle_scores_multi_tick_transitions(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent(
        [
            [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}],
            [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 280}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 90}],
        ]
    )
    result = run(world, agent, max_ticks=3, trace_path=tmp_path / "trace.jsonl")
    assert result["score_breakdown"]["conflicts_delayed"] >= 0
    assert result["score_breakdown"]["conflicts_worsened"] <= 0
    assert result["score_breakdown"]["conflict_resolved"] > 0


def test_conflict_lifecycle_oscillating_commands_track_transitions(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent(
        [
            [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}],
            [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 280}],
            [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 90}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 0}],
        ]
    )
    result = run(world, agent, max_ticks=5, trace_path=tmp_path / "trace.jsonl")
    metrics = result["metrics"]
    assert metrics["conflicts_delayed_count"] > 0
    assert metrics["conflicts_worsened_count"] > 0
    assert metrics["conflict_resolved_count"] > 0
    assert metrics["conflict_reintroduced_count"] > 0
    assert metrics["secondary_conflicts_created_count"] == metrics["conflict_reintroduced_count"]


def test_secondary_conflict_chain_counts_reintroductions_only(tmp_path):
    world = _world_for_predicted_conflict_tests()
    world.aircraft["A3"].x_nm = 6.0
    world.aircraft["A3"].y_nm = -5.0
    world.aircraft["A3"].heading_deg = 270
    agent = ScriptedAgent(
        [
            [{"aircraft": "A1", "type": "assign_heading", "heading": 90}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 0}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 90}],
        ]
    )
    result = run(world, agent, max_ticks=3, trace_path=tmp_path / "trace.jsonl")
    metrics = result["metrics"]
    assert metrics["conflict_introduced_count"] >= 2
    assert metrics["conflict_reintroduced_count"] >= 1
    assert metrics["secondary_conflicts_created_count"] == metrics["conflict_reintroduced_count"]
