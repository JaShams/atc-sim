from pathlib import Path

from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.simulator.engine import load_world, run


def test_run_returns_score(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    result = run(world, HeuristicAgent(), max_ticks=5, trace_path=tmp_path / "trace.jsonl")
    assert "score" in result
    assert "metrics" in result
    assert (tmp_path / "trace.jsonl").exists()
