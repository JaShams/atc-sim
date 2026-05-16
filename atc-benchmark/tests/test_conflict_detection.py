
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


def test_wake_trailing_heavy_requires_extended_minimum() -> None:
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    leader = world.aircraft["ARR1"]
    follower = world.aircraft["ARR2"]
    leader.aircraft_type = "B777"
    leader.wake_category = "heavy"
    leader.x_nm = 0.0
    leader.y_nm = 5.0
    leader.heading_deg = 0.0
    leader.altitude_ft = 5000.0
    follower.wake_category = "light"
    follower.x_nm = 0.0
    follower.y_nm = -1.0
    follower.heading_deg = 0.0
    follower.altitude_ft = 5000.0

    conflicts = detect_conflicts(world)
    assert conflicts
    assert conflicts[0]["required_horizontal_nm"] == 7.0


def test_non_heavy_baseline_minimum_is_unchanged() -> None:
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    a = world.aircraft["ARR1"]
    b = world.aircraft["ARR2"]
    a.aircraft_type = "A320"
    a.wake_category = "medium"
    a.x_nm = 0.0
    a.y_nm = 0.0
    a.altitude_ft = 5000.0
    b.wake_category = "light"
    b.x_nm = 2.5
    b.y_nm = 0.0
    b.altitude_ft = 5000.0

    conflicts = detect_conflicts(world)
    assert conflicts
    assert conflicts[0]["required_horizontal_nm"] == world.rules.min_horizontal_nm
