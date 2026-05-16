from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.engine import advance, apply_actions, load_world
from atc_benchmark.simulator.validator import validate_actions


def test_hold_at_waypoint_and_exit_hold_lifecycle():
    world = load_world(resolve_scenario_path("scenarios/holding_stack_001.json"))
    action = {
        "aircraft": "ARR1",
        "type": "hold_at_waypoint",
        "waypoint": "HOLD1",
        "leg_length_nm": 2.0,
        "turn_direction": "right",
        "hold_altitude_ft": 6000,
    }
    valid, invalid = validate_actions(world, [action])
    assert valid and not invalid
    apply_actions(world, valid)
    assert world.aircraft["ARR1"].status == "holding"

    phases = set()
    for _ in range(20):
        advance(world)
        phases.add(world.aircraft["ARR1"].hold_phase)
    assert "outbound" in phases
    assert "inbound" in phases or "turn_to_inbound" in phases

    exit_action = {"aircraft": "ARR1", "type": "exit_hold"}
    valid, invalid = validate_actions(world, [exit_action])
    assert valid and not invalid
    apply_actions(world, valid)
    assert world.aircraft["ARR1"].hold_fix_id is None
    assert world.aircraft["ARR1"].status == "airborne"


def test_holding_stack_stays_separated_and_contained():
    world = load_world(resolve_scenario_path("scenarios/holding_stack_001.json"))
    actions = [
        {"aircraft": "ARR1", "type": "hold_at_waypoint", "waypoint": "HOLD1", "leg_length_nm": 2.0, "turn_direction": "right", "hold_altitude_ft": 7000},
        {"aircraft": "ARR2", "type": "hold_at_waypoint", "waypoint": "HOLD1", "leg_length_nm": 2.0, "turn_direction": "right", "hold_altitude_ft": 8000},
        {"aircraft": "ARR3", "type": "hold_at_waypoint", "waypoint": "HOLD1", "leg_length_nm": 2.0, "turn_direction": "right", "hold_altitude_ft": 9000},
    ]
    valid, invalid = validate_actions(world, actions)
    assert len(valid) == 3 and not invalid
    apply_actions(world, valid)

    for _ in range(40):
        advance(world)

    for callsign in ("ARR1", "ARR2", "ARR3"):
        ac = world.aircraft[callsign]
        assert ac.status == "holding"
        assert abs(ac.x_nm) < 20 and abs(ac.y_nm) < 20

    assert world.aircraft["ARR1"].altitude_ft < world.aircraft["ARR2"].altitude_ft < world.aircraft["ARR3"].altitude_ft
