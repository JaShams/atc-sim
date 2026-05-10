from pathlib import Path
from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.validator import validate_actions


def test_invalid_heading_rejected():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    _, invalid = validate_actions(world, [{"aircraft": "ARR1", "type": "assign_heading", "heading": 400}])
    assert invalid and invalid[0]["reason"] == "invalid_heading"
