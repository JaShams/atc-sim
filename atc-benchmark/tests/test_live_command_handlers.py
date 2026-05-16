from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.runner.live_commands import handle_http_command, handle_ws_envelope
from atc_benchmark.simulator.engine import load_world


def _world():
    return load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))


def test_ws_command_ack_and_mutates_worldstate():
    world = _world()
    resp = handle_ws_envelope(
        world,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 210},
        },
    )
    assert resp["status"] == "ack"
    assert resp["ok"] is True
    assert resp["type"] == "command_ack"
    assert world.aircraft["ARR1"].heading_deg == 210


def test_ws_command_nack_with_validator_reason_and_no_mutation():
    world = _world()
    before = world.aircraft["ARR1"].heading_deg
    resp = handle_ws_envelope(
        world,
        {
            "type": "command",
            "session_id": "abc",
            "command": {"aircraft": "ARR1", "type": "assign_heading", "heading": 999},
        },
    )
    assert resp["status"] == "nack"
    assert resp["reason"] == "invalid_heading"
    assert resp["details"]["rejected_action"]["heading"] == 999
    assert world.aircraft["ARR1"].heading_deg == before


def test_http_command_accepts_envelope_or_direct_action():
    world = _world()
    envelope_resp = handle_http_command(
        world,
        {"type": "command", "command": {"aircraft": "ARR1", "type": "assign_speed", "speed_kt": 230}},
    )
    assert envelope_resp["status"] == "ack"
    direct_resp = handle_http_command(world, {"aircraft": "ARR1", "type": "assign_altitude", "altitude_ft": 4000})
    assert direct_resp["status"] == "ack"
    assert world.aircraft["ARR1"].speed_kt == 230
    assert world.aircraft["ARR1"].target_altitude_ft == 4000
