from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.runner.live_commands import handle_http_command, handle_ws_envelope
from atc_benchmark.simulator.engine import SimulationStepper, load_world


def _stepper():
    return SimulationStepper(load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json")))


def _step(stepper):
    stepper.begin_tick()
    stepper.finish_tick()


def test_ws_command_ack_then_applies_on_next_tick():
    stepper = _stepper()
    world = stepper.world
    resp = handle_ws_envelope(
        stepper,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 210},
        },
    )
    assert resp["status"] == "ack"
    assert resp["ok"] is True
    assert resp["type"] == "command_ack"
    assert resp["details"]["scheduled_execution_time_sec"] == world.time_sec
    # Read back but not yet executed: execution happens on the next tick.
    assert world.aircraft["ARR1"].heading_deg != 210
    _step(stepper)
    assert world.aircraft["ARR1"].heading_deg == 210


def test_ws_command_nack_with_validator_reason_and_no_mutation():
    stepper = _stepper()
    world = stepper.world
    before = world.aircraft["ARR1"].heading_deg
    resp = handle_ws_envelope(
        stepper,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 999},
        },
    )
    assert resp["status"] == "nack"
    assert resp["reason"] == "invalid_heading"
    assert resp["details"]["rejected_action"]["heading"] == 999
    _step(stepper)
    assert world.aircraft["ARR1"].heading_deg == before


def test_http_command_accepts_envelope_or_direct_action():
    stepper = _stepper()
    world = stepper.world
    envelope_resp = handle_http_command(
        stepper,
        {"type": "command", "command": {"aircraft": "ARR1", "type": "assign_speed", "speed_kt": 230}},
    )
    assert envelope_resp["status"] == "ack"
    direct_resp = handle_http_command(stepper, {"aircraft": "ARR1", "type": "assign_altitude", "altitude_ft": 4000})
    assert direct_resp["status"] == "ack"
    _step(stepper)
    assert world.aircraft["ARR1"].speed_kt == 230
    assert world.aircraft["ARR1"].target_altitude_ft == 4000


def test_live_command_respects_pilot_readback_delay():
    stepper = _stepper()
    world = stepper.world
    world.rules.pilot_readback_delay_sec = {"min": 10, "max": 10}
    resp = handle_ws_envelope(
        stepper,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 210},
        },
    )
    assert resp["status"] == "ack"
    scheduled = resp["details"]["scheduled_execution_time_sec"]
    assert scheduled == world.time_sec + 10
    # tick_sec is 5: the command must not execute until the schedule elapses.
    _step(stepper)
    assert world.aircraft["ARR1"].heading_deg != 210
    _step(stepper)
    _step(stepper)
    assert world.aircraft["ARR1"].heading_deg == 210


def test_live_invalid_command_counts_against_score():
    stepper = _stepper()
    handle_ws_envelope(
        stepper,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 999},
        },
    )
    _step(stepper)
    score = stepper.build_score()
    assert score["control_quality"]["invalid_commands"] == 1
    assert score["score_breakdown"]["invalid_command"] < 0
