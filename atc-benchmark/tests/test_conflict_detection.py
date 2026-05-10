from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.conflict_detection import detect_conflicts
from pathlib import Path


def test_detect_conflict_from_scenario():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    world.aircraft["ARR1"].x_nm = 0
    world.aircraft["ARR1"].y_nm = 0
    world.aircraft["ARR2"].x_nm = 1
    world.aircraft["ARR2"].y_nm = 1
    conflicts = detect_conflicts(world)
    assert conflicts
