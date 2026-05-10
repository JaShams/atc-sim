import json
from pathlib import Path

from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.runner.batch_evaluate import main as batch_main
from atc_benchmark.runner.run_scenario import build_manifest
from atc_benchmark.simulator.engine import load_world, run


class BadAgent:
    def act(self, _obs):
        return {"actions": "not-a-list"}


def test_malformed_agent_output_becomes_invalid_command(tmp_path):
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    manifest = build_manifest(resolve_scenario_path("scenarios/crossing_conflict_001.json"), world, "bad", 1)
    result = run(world, BadAgent(), max_ticks=1, trace_path=tmp_path / "trace.jsonl", manifest=manifest)
    assert result["control_quality"]["invalid_commands"] >= 1
    assert result["metrics"]["malformed_agent_outputs_count"] >= 1


def test_new_metric_fields_and_types(tmp_path):
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    manifest = build_manifest(resolve_scenario_path("scenarios/crossing_conflict_001.json"), world, "noop", 2)
    result = run(world, BadAgent(), max_ticks=1, trace_path=tmp_path / "trace.jsonl", manifest=manifest)
    metrics = result["metrics"]
    assert isinstance(metrics["active_conflicts_count_total"], int)
    assert isinstance(metrics["predicted_conflicts_count_total"], int)
    assert isinstance(metrics["runway_unsafe_clearance_count"], int)
    assert isinstance(metrics["malformed_agent_outputs_count"], int)
    assert isinstance(metrics["throughput_ops_per_hour"], float)


def test_run_manifest_reproducible_fields(tmp_path):
    scenario = resolve_scenario_path("scenarios/crossing_conflict_001.json")
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
    assert "total_active_conflicts_count" in summary
    assert "total_predicted_conflicts_count" in summary
    assert "total_runway_unsafe_clearances" in summary
    assert "total_malformed_agent_outputs" in summary
    assert "average_throughput_ops_per_hour" in summary
    first = summary["scenarios"][0]["scenario"].replace(".json", "")
    assert "active_conflicts_count_total" in summary["scenarios"][0]
    assert "predicted_conflicts_count_total" in summary["scenarios"][0]
    assert "runway_unsafe_clearance_count" in summary["scenarios"][0]
    assert "malformed_agent_outputs_count" in summary["scenarios"][0]
    assert "throughput_ops_per_hour" in summary["scenarios"][0]
    assert (out / "scores" / f"{first}.json").exists()
    assert (out / "traces" / f"{first}.jsonl").exists()


def test_batch_evaluator_multi_agent_outputs(tmp_path, monkeypatch):
    out = tmp_path / "batch_multi"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    import sys

    sys.argv = ["atc-batch", "--agents", "heuristic,noop,random", "--output-dir", str(out), "--max-ticks", "2"]
    batch_main()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["agents"] == ["heuristic", "noop", "random"]
    assert summary["count"] >= 3
    assert "expected_baseline_pass" in summary["scenarios"][0]
    for agent in ["heuristic", "noop", "random"]:
        assert (out / agent / "scores").exists()
        assert (out / agent / "traces").exists()
