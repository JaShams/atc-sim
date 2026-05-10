from pathlib import Path

from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.validator import validate_actions


def test_invalid_heading_rejected():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "assign_heading", "heading": 400}])
    assert invalid and invalid[0]["reason"] == "invalid_heading"


def test_takeoff_rejected_if_arrival_too_close():
    world = load_world(Path("scenarios/departure_between_arrivals_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert invalid and invalid[0]["reason"] == "arrival_too_close"


def test_landing_clearance_requires_arrival_state():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_to_land"}])
    assert invalid and invalid[0]["reason"] == "not_arrival"
