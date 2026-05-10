
from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.conflict_detection import detect_conflicts, predict_conflicts
from atc_benchmark.simulator.engine import load_world


def test_detect_conflict_from_scenario():
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    world.aircraft["ARR1"].x_nm = 0
    world.aircraft["ARR1"].y_nm = 0
    world.aircraft["ARR2"].x_nm = 1
    world.aircraft["ARR2"].y_nm = 1
    conflicts = detect_conflicts(world)
    assert conflicts


def test_predict_conflict_uses_lookahead_window():
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    world.rules.lookahead_seconds = 120
    preds = predict_conflicts(world)
    assert preds
    assert min(p["in_seconds"] for p in preds) <= 120
