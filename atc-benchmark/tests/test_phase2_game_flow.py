"""Phase 2 regression tests: level catalog, win/lose outcomes, debrief, stars."""

import asyncio
import json
from pathlib import Path

from atc_benchmark.paths import scenarios_dir
from atc_benchmark.runner.game_session import build_mission_debrief, star_rating
from atc_benchmark.runner.live_server import LiveGameServer, list_levels


def _score(**overrides):
    base = {
        "score": 90.0,
        "metrics": {"emergency_unhandled_count": 0, "restricted_zone_violation_count": 0, "malformed_agent_outputs_count": 0},
        "safety": {"loss_of_separation": 0},
        "efficiency": {"successful_landings": 2, "successful_departures": 1},
        "control_quality": {"invalid_commands": 0},
    }
    for key, value in overrides.items():
        if isinstance(value, dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


# --- star rating ---

def test_star_rating_default_thresholds():
    assert star_rating(0.0) == 0
    assert star_rating(30.0) == 1
    assert star_rating(60.0) == 2
    assert star_rating(95.0) == 3


def test_star_rating_metadata_override():
    metadata = {"star_thresholds": [10.0, 50.0, 99.0]}
    assert star_rating(95.0, metadata) == 2
    assert star_rating(99.0, metadata) == 3


# --- debrief outcomes ---

def test_debrief_success_failure_and_override():
    assert build_mission_debrief(_score()).outcome == "success"
    assert build_mission_debrief(_score(safety={"loss_of_separation": 2})).outcome == "failure"
    crashed = build_mission_debrief(_score(), outcome_override="crashed")
    assert crashed.outcome == "crashed"
    assert "CRASHED" in crashed.headline
    assert crashed.to_dict()["details"]


def test_ending_session_early_forfeits_stars(tmp_path):
    async def scenario():
        game = LiveGameServer(max_ticks=50, tick_interval_sec=0, output_root=tmp_path / "out")
        queue = await game.transport.subscribe_tick_stream()
        await game.on_command({"type": "start_level", "scenario": "crossing_conflict_001.json"})
        loop_task = asyncio.create_task(game.run_loop())
        try:
            await _drain(queue, want_type="tick", limit=10)
            await game.on_command({"type": "end_session"})
            done = await _drain(queue, want_type="level_complete", limit=60)
        finally:
            loop_task.cancel()
        assert done["outcome"] == "ended_by_user"
        assert done["debrief"]["outcome"] == "abandoned"
        assert done["stars"] == 0

    asyncio.run(scenario())


def test_debrief_timeout_when_emergency_unhandled_and_no_ops():
    score = _score(
        metrics={"emergency_unhandled_count": 1},
        efficiency={"successful_landings": 0, "successful_departures": 0},
    )
    assert build_mission_debrief(score).outcome == "timeout"


# --- level catalog ---

def test_list_levels_covers_all_scenarios_with_metadata():
    levels = list_levels()
    files = sorted(p.name for p in scenarios_dir().glob("*.json"))
    assert [lvl["id"] for lvl in levels] == files
    for lvl in levels:
        assert lvl["name"]
        assert isinstance(lvl["tags"], list)
        assert lvl["aircraft_count"] >= 1
        assert lvl["arrivals"] + lvl["departures"] <= lvl["aircraft_count"]


# --- live game server lifecycle ---

async def _drain(queue, *, want_type, limit=20):
    for _ in range(limit):
        envelope = await asyncio.wait_for(queue.get(), timeout=2)
        if envelope.get("type") == want_type:
            return envelope
    raise AssertionError(f"no {want_type} envelope received")


def test_live_game_server_level_select_flow(tmp_path):
    async def scenario():
        game = LiveGameServer(max_ticks=10, tick_interval_sec=0, output_root=tmp_path)
        queue = await game.transport.subscribe_tick_stream()

        listing = await game.on_command({"type": "list_levels"})
        assert listing["type"] == "level_list"
        assert any(lvl["id"] == "crossing_conflict_001.json" for lvl in listing["levels"])

        unknown = await game.on_command({"type": "start_level", "scenario": "../evil.json"})
        assert unknown["ok"] is False and unknown["status"] == "unknown_level"

        before_start = await game.on_command(
            {"type": "command", "command": {"aircraft": "ARR1", "type": "no_op"}}
        )
        assert before_start["ok"] is False and before_start["reason"] == "no_active_level"

        started = await game.on_command({"type": "start_level", "scenario": "crossing_conflict_001.json"})
        assert started["ok"] is True and started["level"]["id"] == "crossing_conflict_001.json"
        assert game.level_active

        level_started = await _drain(queue, want_type="level_started")
        assert level_started["level"]["id"] == "crossing_conflict_001.json"
        first_tick = await _drain(queue, want_type="tick")
        assert first_tick["tick"]["time"] == 0

    asyncio.run(scenario())


def test_live_game_server_crash_ends_level_with_zero_stars(tmp_path):
    scenario_doc = {
        "airport": {"runway_id": "09", "active_runway": "09"},
        "aircraft": [
            {"callsign": "A1", "role": "arrival", "x_nm": 10.0, "y_nm": 10.0, "altitude_ft": 5000, "speed_kt": 200, "heading_deg": 90, "status": "airborne"},
        ],
        "events": [
            {"type": "low_fuel_emergency", "aircraft": "A1", "time_sec": 0, "remaining_endurance_sec": 5},
        ],
        "scenario_metadata": {"difficulty_tier": "test"},
    }
    levels_dir = tmp_path / "scenarios"
    levels_dir.mkdir()
    (levels_dir / "crash_test_001.json").write_text(json.dumps(scenario_doc))

    async def scenario():
        game = LiveGameServer(scenarios_root=levels_dir, max_ticks=10, tick_interval_sec=0, output_root=tmp_path / "out")
        queue = await game.transport.subscribe_tick_stream()
        started = await game.on_command({"type": "start_level", "scenario": "crash_test_001.json"})
        assert started["ok"] is True

        loop_task = asyncio.create_task(game.run_loop())
        try:
            done = await _drain(queue, want_type="level_complete", limit=40)
        finally:
            loop_task.cancel()
        assert done["outcome"] == "aircraft_lost"
        assert done["debrief"]["outcome"] == "crashed"
        assert done["stars"] == 0
        assert done["score"]["metrics"]["session_outcome"] == "aircraft_lost"
        assert not game.level_active

    asyncio.run(scenario())


def test_live_game_server_completion_outcome_and_artifacts(tmp_path):
    scenario_doc = {
        "airport": {"runway_id": "18", "active_runway": "18"},
        "aircraft": [
            {
                "callsign": "A1",
                "role": "arrival",
                "x_nm": 0.0,
                "y_nm": 0.5,
                "altitude_ft": 200,
                "speed_kt": 140,
                "heading_deg": 180,
                "status": "on_final",
                "clearance": "cleared_to_land",
            },
        ],
        "scenario_metadata": {},
    }
    levels_dir = tmp_path / "scenarios"
    levels_dir.mkdir()
    (levels_dir / "easy_landing_001.json").write_text(json.dumps(scenario_doc))

    async def scenario():
        game = LiveGameServer(scenarios_root=levels_dir, max_ticks=20, tick_interval_sec=0, output_root=tmp_path / "out")
        queue = await game.transport.subscribe_tick_stream()
        await game.on_command({"type": "start_level", "scenario": "easy_landing_001.json"})

        loop_task = asyncio.create_task(game.run_loop())
        try:
            done = await _drain(queue, want_type="level_complete", limit=60)
        finally:
            loop_task.cancel()
        assert done["outcome"] == "all_traffic_handled"
        assert done["debrief"]["outcome"] == "success"
        assert done["stars"] >= 2
        assert done["score"]["efficiency"]["successful_landings"] == 1
        assert Path(done["artifacts"]["trace"]).exists()
        assert Path(done["artifacts"]["score"]).exists()

    asyncio.run(scenario())
