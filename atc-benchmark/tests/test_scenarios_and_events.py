import json
from pathlib import Path

from atc_benchmark.agents.base import Agent
from atc_benchmark.agents.noop_agent import NoOpAgent
from atc_benchmark.paths import resolve_scenario_path, scenarios_dir
from atc_benchmark.simulator.decision_points import detect_decision_points
from atc_benchmark.simulator.engine import apply_events, load_world, run


class UnsafeAfterWindAgent(Agent):
    def act(self, obs: dict) -> str:
        dps = obs.get("decision_points", [])
        if any(dp.get("type") == "wind_runway_mismatch" for dp in dps):
            return {"actions": [
                {"type": "clear_for_takeoff", "aircraft": "DEP1"},
                {"type": "clear_to_land", "aircraft": "ARR1"},
            ]}
        return {"actions": []}


class EmergencyPriorityViolationAgent(Agent):
    def act(self, _obs: dict) -> str:
        return {"actions": [{"type": "clear_for_takeoff", "aircraft": "DEP2"}]}


class EmergencyPriorityCompliantAgent(Agent):
    def act(self, _obs: dict) -> str:
        return {"actions": [{"type": "clear_to_land", "aircraft": "ARR_EMG"}]}


def test_scenarios_are_not_duplicated():
    scenario_dir = scenarios_dir()
    contents = [p.read_text() for p in sorted(scenario_dir.glob("*.json"))]
    assert len(contents) == len(set(contents))


def test_event_triggered_decision_point_and_effect():
    world = load_world(resolve_scenario_path("scenarios/wind_change_runway_switch_001.json"))
    world.time_sec = 15
    events = apply_events(world)
    dps = detect_decision_points(world)
    assert events and events[0]["type"] == "wind_change"
    assert world.airport.active_runway == "09"
    assert any(dp["type"] == "wind_runway_mismatch" for dp in dps)


def test_post_wind_change_metrics_capture_unsafe_clearances():
    world = load_world(resolve_scenario_path("scenarios/wind_change_runway_switch_001.json"))
    result = run(world, UnsafeAfterWindAgent(), max_ticks=5, trace_path=Path("/tmp/wind_trace.jsonl"))
    assert result["metrics"]["wind_response_latency_sec"] is None
    assert result["metrics"]["unsafe_clearances_after_wind_change"] >= 1


def test_expanded_wind_shift_scenario_generates_decision_points():
    world = load_world(resolve_scenario_path("scenarios/wind_shift_active_final_queued_departure_001.json"))
    world.time_sec = 10
    events = apply_events(world)
    dps = detect_decision_points(world)
    assert events and events[0]["type"] == "wind_change"
    assert any(dp["type"] == "wind_runway_mismatch" for dp in dps)
    assert any(dp["type"] == "departure_ready" for dp in dps)
    assert any(dp["type"] == "runway_occupied_on_final" for dp in dps) is False


def test_emergency_priority_compliance_metric_influences_score():
    compliant_world = load_world(resolve_scenario_path("scenarios/emergency_during_runway_occupancy_001.json"))
    compliant = run(compliant_world, EmergencyPriorityCompliantAgent(), max_ticks=2, trace_path=Path("/tmp/emg_compliant.jsonl"))

    violating_world = load_world(resolve_scenario_path("scenarios/emergency_during_runway_occupancy_001.json"))
    violating = run(violating_world, EmergencyPriorityViolationAgent(), max_ticks=2, trace_path=Path("/tmp/emg_violating.jsonl"))

    assert compliant["metrics"]["emergency_priority_compliant_count"] > 0
    assert violating["metrics"]["emergency_priority_violation_count"] > 0
    assert compliant["score_breakdown"]["emergency_priority_compliance"] > violating["score_breakdown"]["emergency_priority_compliance"]


def test_emergency_and_conflict_simultaneous_decision_points():
    world = load_world(resolve_scenario_path("scenarios/emergency_and_conflict_simultaneous_001.json"))
    events = apply_events(world)
    dps = detect_decision_points(world)
    assert events and events[0]["type"] == "emergency_declare"
    assert any(dp["type"] == "emergency" for dp in dps)
    assert any(dp["type"] in {"predicted_conflict", "active_conflict"} for dp in dps)


def test_airport_layout_survives_load_snapshot_and_trace(tmp_path):
    source = json.loads(resolve_scenario_path("scenarios/crossing_conflict_001.json").read_text())
    layout = {
        "runways": [{"id": "09", "ends": [{"x_nm": -2.5, "y_nm": 0.0}, {"x_nm": 2.5, "y_nm": 0.0}]}],
        "taxiways": [{"id": "A", "points": [{"x_nm": -1.0, "y_nm": -0.4}, {"x_nm": 1.0, "y_nm": -0.4}]}],
        "aprons": [
            {
                "id": "MAIN",
                "polygon": [
                    {"x_nm": -0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.5},
                ],
            }
        ],
        "stands": [{"id": "S1", "position": {"x_nm": -0.4, "y_nm": -0.7}}],
    }
    source["airport"]["layout"] = layout
    scenario = tmp_path / "layout_scenario.json"
    trace = tmp_path / "layout_trace.jsonl"
    scenario.write_text(json.dumps(source))

    world = load_world(scenario)
    assert world.snapshot()["airport"]["layout"] == layout

    run(world, NoOpAgent(), max_ticks=1, trace_path=trace)
    first_tick = json.loads(trace.read_text().splitlines()[0])
    assert first_tick["state"]["airport"]["layout"] == layout
