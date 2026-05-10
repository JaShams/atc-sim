from pathlib import Path

from atc_benchmark.agents.base import Agent
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


def test_scenarios_are_not_duplicated():
    scenario_dir = Path("scenarios")
    contents = [p.read_text() for p in sorted(scenario_dir.glob("*.json"))]
    assert len(contents) == len(set(contents))


def test_event_triggered_decision_point_and_effect():
    world = load_world(Path("scenarios/wind_change_runway_switch_001.json"))
    world.time_sec = 15
    events = apply_events(world)
    dps = detect_decision_points(world)
    assert events and events[0]["type"] == "wind_change"
    assert world.airport.active_runway == "09"
    assert any(dp["type"] == "wind_runway_mismatch" for dp in dps)


def test_post_wind_change_metrics_capture_unsafe_clearances():
    world = load_world(Path("scenarios/wind_change_runway_switch_001.json"))
    result = run(world, UnsafeAfterWindAgent(), max_ticks=5, trace_path=Path("/tmp/wind_trace.jsonl"))
    assert result["metrics"]["wind_change_response_latency_sec"] is None
    assert result["metrics"]["unsafe_clearances_after_wind_change"] >= 1
