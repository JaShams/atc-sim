from pathlib import Path

from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.validator import validate_actions


def test_invalid_heading_rejected():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "assign_heading", "heading": 400}])
    assert invalid and invalid[0]["reason"] == "invalid_heading"


def test_takeoff_rejected_if_arrival_too_close():
    world = load_world(Path("scenarios/departure_between_arrivals_001.json"))
    world.aircraft["ARR1"].x_nm = 4.0
    world.aircraft["ARR1"].y_nm = 0.0
    world.aircraft["ARR1"].heading_deg = 270
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert invalid and invalid[0]["reason"] == "arrival_too_close"


def test_landing_clearance_requires_arrival_state():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_to_land"}])
    assert invalid and invalid[0]["reason"] == "not_arrival"


def test_clearance_rejected_during_projected_runway_occupancy_window():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    world.airport.runway_occupied_by = "ARR1"
    world.airport.occupied_until_sec = world.time_sec + 20
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert invalid and invalid[0]["reason"] == "runway_occupied"


def test_second_runway_clearance_in_same_tick_rejected():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    actions = [
        {"aircraft": "ARR1", "type": "clear_to_land"},
        {"aircraft": "DEP1", "type": "clear_for_takeoff"},
    ]
    _, invalid = validate_actions(world, actions)
    assert invalid and invalid[0]["reason"] == "runway_occupied"
