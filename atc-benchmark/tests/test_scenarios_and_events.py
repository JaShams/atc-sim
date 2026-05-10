import json
from pathlib import Path

from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.decision_points import detect_decision_points


def test_scenarios_are_not_duplicated():
    scenario_dir = Path("scenarios")
    contents = [p.read_text() for p in sorted(scenario_dir.glob("*.json"))]
    assert len(contents) == len(set(contents))


def test_event_triggered_decision_point_and_effect():
    world = load_world(Path("scenarios/wind_change_runway_switch_001.json"))
    world.time_sec = 15
    from atc_benchmark.simulator.engine import apply_events

    events = apply_events(world)
    dps = detect_decision_points(world)
    assert events and events[0]["type"] == "wind_change"
    assert world.airport.active_runway == "27"
    assert isinstance(dps, list)
