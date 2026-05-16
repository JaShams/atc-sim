from __future__ import annotations

import io
import json
import select
import sys
from pathlib import Path

import pytest

from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.runner.game_session import GameSession
from atc_benchmark.runner.run_scenario import main as run_scenario_main
from atc_benchmark.simulator.engine import load_world


@pytest.fixture
def human_stdin_fixture(monkeypatch):
    def _install(lines: list[str]):
        payload = "\n".join(lines) + "\n"
        fake_stdin = io.StringIO(payload)
        monkeypatch.setattr(sys, "stdin", fake_stdin)

        def _always_ready(readable, _w, _x, _timeout=None):
            return (readable, [], [])

        monkeypatch.setattr(select, "select", _always_ready)

    return _install


def _run_human_cli(tmp_path: Path, monkeypatch, human_stdin_fixture, *, commands: list[str], max_ticks: int = 3):
    trace_path = tmp_path / "trace.jsonl"
    score_path = tmp_path / "score.json"
    scenario = resolve_scenario_path("scenarios/crossing_conflict_001.json")
    human_stdin_fixture(commands)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atc-run",
            str(scenario),
            "--agent",
            "human",
            "--max-ticks",
            str(max_ticks),
            "--human-max-retries",
            "2",
            "--human-timeout-sec",
            "0.01",
            "--trace",
            str(trace_path),
            "--score",
            str(score_path),
        ],
    )
    run_scenario_main()
    trace_rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    score = json.loads(score_path.read_text())
    return trace_rows, score


def test_human_agent_invalid_input_retries_then_noop(tmp_path, monkeypatch, human_stdin_fixture):
    trace_rows, score = _run_human_cli(
        tmp_path,
        monkeypatch,
        human_stdin_fixture,
        commands=["not-json", "still-not-json", "no_op"],
        max_ticks=1,
    )

    assert trace_rows[0]["actions"] == []
    assert score["control_quality"]["instructions_issued"] == 0


def test_human_manual_script_is_deterministic_across_runs(tmp_path, monkeypatch, human_stdin_fixture):
    commands = [
        '{"aircraft":"ARR1","type":"assign_heading","heading":180}',
        "no_op",
        '{"aircraft":"DEP1","type":"clear_for_takeoff"}',
    ]
    trace_a, score_a = _run_human_cli(tmp_path / "a", monkeypatch, human_stdin_fixture, commands=commands)
    trace_b, score_b = _run_human_cli(tmp_path / "b", monkeypatch, human_stdin_fixture, commands=commands)

    assert trace_a == trace_b
    assert score_a["score"] == score_b["score"]
    assert score_a["metrics"] == score_b["metrics"]


def test_human_session_outputs_trace_jsonl_and_score_json_with_viewer_keys(tmp_path, monkeypatch, human_stdin_fixture):
    trace_rows, score = _run_human_cli(
        tmp_path,
        monkeypatch,
        human_stdin_fixture,
        commands=["no_op", "no_op"],
        max_ticks=2,
    )

    assert trace_rows
    assert isinstance(score.get("score"), (float, int))
    required_trace_keys = {
        "time",
        "triggered_events",
        "decision_points",
        "observation",
        "actions",
        "invalid_actions",
        "conflicts",
        "predicted_conflicts",
        "state",
        "tick_explanation",
    }
    first = trace_rows[0]
    assert required_trace_keys.issubset(first.keys())
    assert {"call_reason", "trigger_context", "action_chosen", "outcome"}.issubset(first["tick_explanation"].keys())


def test_game_session_smoke_end_of_level_state_transition(tmp_path):
    scenario = resolve_scenario_path("scenarios/crossing_conflict_001.json")
    world = load_world(scenario)

    class QuickTakeoffAgent:
        def act(self, obs):
            departures = [
                cs
                for cs, ac in obs["snapshot"]["aircraft"].items()
                if ac.get("role") == "departure" and ac.get("status") == "taxi"
            ]
            if departures:
                return {"actions": [{"aircraft": departures[0], "type": "clear_for_takeoff"}]}
            return {"actions": [{"type": "no_op"}]}

    session = GameSession(world, QuickTakeoffAgent(), max_ticks=20, trace_path=tmp_path / "session_trace.jsonl", manifest={})
    result = session.run()

    assert (tmp_path / "session_trace.jsonl").exists()
    assert world.time_sec > 0
    assert result["control_quality"]["instructions_issued"] >= 1
