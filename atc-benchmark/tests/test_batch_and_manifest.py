import json
from pathlib import Path

from atc_benchmark.runner.batch_evaluate import main as batch_main
from atc_benchmark.runner.run_scenario import build_manifest
from atc_benchmark.simulator.engine import load_world, run


class BadAgent:
    def act(self, _obs):
        return {"actions": "not-a-list"}


def test_malformed_agent_output_becomes_invalid_command(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    manifest = build_manifest(Path("scenarios/crossing_conflict_001.json"), world, "bad", 1)
    result = run(world, BadAgent(), max_ticks=1, trace_path=tmp_path / "trace.jsonl", manifest=manifest)
    assert result["control_quality"]["invalid_commands"] >= 1


def test_run_manifest_reproducible_fields(tmp_path):
    scenario = Path("scenarios/crossing_conflict_001.json")
    world = load_world(scenario)
    manifest = build_manifest(scenario, world, "noop", 2)
    result = run(world, BadAgent(), max_ticks=1, trace_path=tmp_path / "trace.jsonl", manifest=manifest)
    rm = result["run_manifest"]
    assert rm["scenario_file"] == "crossing_conflict_001.json"
    assert isinstance(rm["scenario_hash"], str) and len(rm["scenario_hash"]) == 64
    assert rm["tick_sec"] == world.tick_sec
    assert "rules_config" in rm and "scoring_config" in rm


def test_batch_evaluator_outputs(tmp_path, monkeypatch):
    out = tmp_path / "batch"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    import sys

    sys.argv = ["atc-batch", "--agent", "noop", "--output-dir", str(out), "--max-ticks", "2"]
    batch_main()
    assert (out / "summary.json").exists()
    assert (out / "summary.csv").exists()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["count"] > 0
    first = summary["scenarios"][0]["scenario"].replace(".json", "")
    assert (out / "scores" / f"{first}.json").exists()
    assert (out / "traces" / f"{first}.jsonl").exists()
