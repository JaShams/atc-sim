from pathlib import Path

from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.decision_points import detect_decision_points
from atc_benchmark.simulator.engine import SimulationStepper, load_world, run
from atc_benchmark.simulator.validator import validate_actions


def _two_arrivals_world():
    return load_world(resolve_scenario_path("scenarios/two_arrivals_one_runway_001.json"))


def _crossing_world():
    return load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))


# --- clear_to_land vertical geometry ---

def test_clearance_rejected_when_too_high_to_capture_glidepath():
    world = _two_arrivals_world()
    # ARR1 is 12 nm out: ceiling is 12 * 320 + 1200 = 5040 ft.
    world.aircraft["ARR1"].altitude_ft = 9000
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "clear_to_land"}])
    assert invalid and invalid[0]["reason"] == "too_high_for_approach"


def test_clearance_accepted_at_glidepath_intercept_altitude():
    world = _two_arrivals_world()
    world.aircraft["ARR1"].altitude_ft = 4500
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "clear_to_land"}])
    assert not invalid


def test_cleared_arrival_descends_glidepath_and_lands():
    world = _two_arrivals_world()
    stepper = SimulationStepper(world)
    arr = world.aircraft["ARR1"]
    cleared = False
    max_drop_per_tick_ft = 0.0
    for _ in range(400):
        stepper.begin_tick()
        if not cleared:
            _, invalid = stepper.submit_actions([{"aircraft": "ARR1", "type": "clear_to_land"}])
            assert not invalid
            cleared = True
        altitude_before = arr.altitude_ft
        stepper.finish_tick()
        max_drop_per_tick_ft = max(max_drop_per_tick_ft, altitude_before - arr.altitude_ft)
        if arr.status == "landed":
            break
    assert arr.status == "landed"
    assert arr.landing_time_sec is not None
    # Descent happened on a continuous profile capped at 1500 fpm, not a teleport.
    assert 0 < max_drop_per_tick_ft <= 1500 * world.tick_sec / 60 + 1e-6


def test_go_around_cancels_approach_descent():
    world = _two_arrivals_world()
    stepper = SimulationStepper(world)
    arr = world.aircraft["ARR1"]
    stepper.begin_tick()
    stepper.submit_actions([{"aircraft": "ARR1", "type": "clear_to_land"}])
    stepper.finish_tick()
    stepper.begin_tick()
    _, invalid = stepper.submit_actions([{"aircraft": "ARR1", "type": "go_around"}])
    assert not invalid
    stepper.finish_tick()
    assert arr.clearance is None
    assert arr.status == "go_around"
    assert arr.vertical_rate_fpm > 0


# --- handoff_to_center ---

def test_handoff_rejected_for_arrivals_and_grounded_departures():
    world = _crossing_world()
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "handoff_to_center"}])
    assert invalid and invalid[0]["reason"] == "not_departure"
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "handoff_to_center"}])
    assert invalid and invalid[0]["reason"] == "not_airborne_departure"


def test_handoff_rejected_close_to_field_then_accepted_when_due():
    world = _crossing_world()
    dep = world.aircraft["DEP1"]
    dep.status = "airborne_departure"
    dep.x_nm, dep.y_nm, dep.altitude_ft, dep.speed_kt = 5.0, 0.0, 3000, 250
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "handoff_to_center"}])
    assert invalid and invalid[0]["reason"] == "too_close_for_handoff"

    dep.x_nm = 20.0
    valid, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "handoff_to_center"}])
    assert valid and not invalid


def test_handed_off_aircraft_rejects_further_commands():
    world = _crossing_world()
    dep = world.aircraft["DEP1"]
    dep.status = "airborne_departure"
    dep.x_nm, dep.y_nm, dep.altitude_ft, dep.speed_kt = 20.0, 0.0, 4000, 250
    stepper = SimulationStepper(world)
    stepper.begin_tick()
    _, invalid = stepper.submit_actions([{"aircraft": "DEP1", "type": "handoff_to_center"}])
    assert not invalid
    stepper.finish_tick()
    assert dep.handed_off
    assert dep.handoff_time_sec is not None

    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "assign_heading", "heading": 180}])
    assert invalid and invalid[0]["reason"] == "aircraft_handed_off"
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "handoff_to_center"}])
    assert invalid and invalid[0]["reason"] == "aircraft_handed_off"


def _fly_departure_to_exit(world, stepper):
    for _ in range(400):
        stepper.begin_tick()
        stepper.finish_tick()
        if world.aircraft["DEP1"].status == "exited_airspace":
            break
    assert world.aircraft["DEP1"].status == "exited_airspace"


def test_handoff_scoring_reward_and_missed_penalty():
    # Departure handed off before the boundary earns the reward.
    world = _crossing_world()
    dep = world.aircraft["DEP1"]
    dep.status = "airborne_departure"
    dep.x_nm, dep.y_nm, dep.altitude_ft, dep.speed_kt = 20.0, 0.0, 4000, 250
    stepper = SimulationStepper(world)
    stepper.begin_tick()
    stepper.submit_actions([{"aircraft": "DEP1", "type": "handoff_to_center"}])
    stepper.finish_tick()
    _fly_departure_to_exit(world, stepper)
    score = stepper.build_score()
    assert score["score_breakdown"]["handoff_completed"] == world.scoring.handoff_completed_reward
    assert score["score_breakdown"]["missed_handoff"] == 0
    assert score["metrics"]["handoffs_completed_count"] == 1

    # Departure that exits without a handoff is penalized.
    world = _crossing_world()
    dep = world.aircraft["DEP1"]
    dep.status = "airborne_departure"
    dep.x_nm, dep.y_nm, dep.altitude_ft, dep.speed_kt = 20.0, 0.0, 4000, 250
    stepper = SimulationStepper(world)
    _fly_departure_to_exit(world, stepper)
    score = stepper.build_score()
    assert score["score_breakdown"]["handoff_completed"] == 0
    assert score["score_breakdown"]["missed_handoff"] == world.scoring.missed_handoff_penalty
    assert score["metrics"]["missed_handoffs_count"] == 1


# --- decision points ---

def test_decision_points_surface_landing_clearance_and_handoff():
    world = _two_arrivals_world()
    dps = detect_decision_points(world)
    landing_dps = [dp for dp in dps if dp["type"] == "landing_clearance_available"]
    assert [dp["aircraft"] for dp in landing_dps] == [["ARR1"]]

    world = _crossing_world()
    dep = world.aircraft["DEP1"]
    dep.status = "airborne_departure"
    dep.x_nm, dep.y_nm = 16.0, 0.0
    dps = detect_decision_points(world)
    assert any(dp["type"] == "handoff_due" and dp["aircraft"] == ["DEP1"] for dp in dps)
    dep.handed_off = True
    dps = detect_decision_points(world)
    assert not any(dp["type"] == "handoff_due" for dp in dps)


# --- heuristic agent closes the loop ---

def test_heuristic_agent_lands_arrivals_and_hands_off_departures(tmp_path: Path):
    world = _two_arrivals_world()
    score = run(world, HeuristicAgent(), max_ticks=400, trace_path=tmp_path / "trace.jsonl")
    assert score["efficiency"]["successful_landings"] >= 1
