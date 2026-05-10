from pathlib import Path

from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.models import ALLOWED_ACTION_TYPES
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
    world.airport.runway_occupied_until_sec = world.time_sec + 20
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


def test_consecutive_arrivals_second_clearance_rejected_by_predicted_occupancy_window():
    world = load_world(Path("scenarios/two_arrivals_one_runway_001.json"))
    actions = [
        {"aircraft": "ARR1", "type": "clear_to_land"},
        {"aircraft": "ARR2", "type": "clear_to_land"},
    ]
    _, invalid = validate_actions(world, actions)
    assert invalid and invalid[0]["reason"] == "runway_occupied"


def test_departure_insertion_between_arrivals_rejected_by_predicted_occupancy_window():
    world = load_world(Path("scenarios/departure_between_arrivals_001.json"))
    world.aircraft["ARR1"].x_nm = -8.0
    world.aircraft["ARR1"].y_nm = 0.0
    world.aircraft["ARR1"].heading_deg = 90
    actions = [
        {"aircraft": "ARR1", "type": "clear_to_land"},
        {"aircraft": "DEP1", "type": "clear_for_takeoff"},
        {"aircraft": "ARR2", "type": "clear_to_land"},
    ]
    _, invalid = validate_actions(world, actions)
    assert len(invalid) == 2
    assert all(item["reason"] == "runway_occupied" for item in invalid)


def test_every_allowed_action_is_applied_or_explicitly_rejected():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    # Keep runway unavailable so runway-clearance actions are deterministically rejected.
    world.airport.runway_occupied_by = "ARR1"
    world.airport.runway_occupied_until_sec = world.time_sec + world.tick_sec

    sample_actions = {
        "assign_heading": {"aircraft": "ARR1", "type": "assign_heading", "heading": 180},
        "assign_altitude": {"aircraft": "ARR1", "type": "assign_altitude", "altitude_ft": 3000},
        "assign_speed": {"aircraft": "ARR1", "type": "assign_speed", "speed_kt": 190},
        "clear_to_land": {"aircraft": "ARR1", "type": "clear_to_land"},
        "clear_for_takeoff": {"aircraft": "DEP1", "type": "clear_for_takeoff"},
        "go_around": {"aircraft": "ARR1", "type": "go_around"},
        "hold_short": {"aircraft": "DEP1", "type": "hold_short"},
        "hold_position": {"aircraft": "DEP1", "type": "hold_position"},
        "no_op": {"aircraft": "ARR1", "type": "no_op"},
    }
    engine_applied_types = {
        "assign_heading",
        "assign_altitude",
        "assign_speed",
        "clear_to_land",
        "clear_for_takeoff",
        "go_around",
        "hold_short",
        "hold_position",
        "no_op",
    }

    assert ALLOWED_ACTION_TYPES == set(sample_actions)
    for action_type in ALLOWED_ACTION_TYPES:
        valid, invalid = validate_actions(world, [sample_actions[action_type]])
        if valid:
            assert action_type in engine_applied_types
            continue
        assert invalid
        assert invalid[0]["reason"]
