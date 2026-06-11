
from itertools import combinations

from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.conflict_detection import _candidate_pairs, detect_conflicts, predict_conflicts
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
    world.aircraft["ARR1"].x_nm = 0
    world.aircraft["ARR1"].y_nm = 0
    world.aircraft["ARR2"].x_nm = 0.2
    world.aircraft["ARR2"].y_nm = 0.2
    world.aircraft["ARR1"].altitude_ft = 4000
    world.aircraft["ARR2"].altitude_ft = 4000
    world.aircraft["ARR1"].status = "airborne"
    world.aircraft["ARR2"].status = "airborne"
    world.aircraft["ARR1"].heading_deg = world.aircraft["ARR2"].heading_deg = 90
    world.aircraft["ARR1"].speed_kt = world.aircraft["ARR2"].speed_kt = 220
    preds = predict_conflicts(world)
    assert preds
    assert min(p["in_seconds"] for p in preds) <= 120


def test_parallel_ils_exception_applies_when_established_on_distinct_centerlines():
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    world.airport.layout = {
        "runways": [
            {
                "id": "09",
                "ends": [{"x_nm": -2.5, "y_nm": 0.0}, {"x_nm": 2.5, "y_nm": 0.0}],
                "ils_centerline": {"start": {"x_nm": 0.0, "y_nm": -10.0}, "end": {"x_nm": 0.0, "y_nm": 0.0}},
                "final_approach_envelope": {"max_distance_nm": 10.0, "min_altitude_ft": 1500, "max_altitude_ft": 5000},
            },
            {
                "id": "10",
                "ends": [{"x_nm": 0.3, "y_nm": -2.5}, {"x_nm": 0.3, "y_nm": 2.5}],
                "ils_centerline": {"start": {"x_nm": 0.3, "y_nm": -10.0}, "end": {"x_nm": 0.3, "y_nm": 0.0}},
                "final_approach_envelope": {"max_distance_nm": 10.0, "min_altitude_ft": 1500, "max_altitude_ft": 5000},
            },
        ],
        "parallel_runway_pairs": [{"runway_a": "09", "runway_b": "10", "established_tolerance_nm": 0.1}],
    }
    arr1 = world.aircraft["ARR1"]
    arr2 = world.aircraft["ARR2"]
    arr1.x_nm, arr1.y_nm, arr1.altitude_ft, arr1.target_runway = 0.0, -4.0, 3000, "09"
    arr2.x_nm, arr2.y_nm, arr2.altitude_ft, arr2.target_runway = 0.3, -4.2, 3100, "10"
    conflicts = detect_conflicts(world)
    assert not conflicts


def test_parallel_ils_exception_not_applied_for_near_miss_not_established():
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    world.airport.layout = {
        "runways": [
            {
                "id": "09",
                "ends": [{"x_nm": -2.5, "y_nm": 0.0}, {"x_nm": 2.5, "y_nm": 0.0}],
                "ils_centerline": {"start": {"x_nm": 0.0, "y_nm": -10.0}, "end": {"x_nm": 0.0, "y_nm": 0.0}},
                "final_approach_envelope": {"max_distance_nm": 10.0, "min_altitude_ft": 1500, "max_altitude_ft": 5000},
            },
            {
                "id": "10",
                "ends": [{"x_nm": 0.3, "y_nm": -2.5}, {"x_nm": 0.3, "y_nm": 2.5}],
                "ils_centerline": {"start": {"x_nm": 0.3, "y_nm": -10.0}, "end": {"x_nm": 0.3, "y_nm": 0.0}},
                "final_approach_envelope": {"max_distance_nm": 10.0, "min_altitude_ft": 1500, "max_altitude_ft": 5000},
            },
        ],
        "parallel_runway_pairs": [{"runway_a": "09", "runway_b": "10", "established_tolerance_nm": 0.1}],
    }
    arr1 = world.aircraft["ARR1"]
    arr2 = world.aircraft["ARR2"]
    arr1.x_nm, arr1.y_nm, arr1.altitude_ft, arr1.target_runway = 0.0, -4.0, 3000, "09"
    arr2.x_nm, arr2.y_nm, arr2.altitude_ft, arr2.target_runway = 0.05, -4.2, 3100, "10"
    conflicts = detect_conflicts(world)
    assert conflicts


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


def test_spatial_pruning_keeps_small_scenario_conflict_results() -> None:
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}]
    baseline_pairs = {(a.callsign, b.callsign) for a, b in combinations(ac_list, 2)}
    pruned_pairs = {tuple(sorted((a.callsign, b.callsign))) for a, b in _candidate_pairs(ac_list, world.rules.min_horizontal_nm)}
    assert pruned_pairs.issubset({tuple(sorted(p)) for p in baseline_pairs})
    assert detect_conflicts(world) == detect_conflicts(world)


def test_spatial_pruning_reduces_candidate_pairs_on_sparse_aircraft() -> None:
    world = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json"))
    world.aircraft.clear()
    n = 80
    for i in range(n):
        template = load_world(resolve_scenario_path("scenarios/crossing_conflict_001.json")).aircraft["ARR1"]
        template.callsign = f"T{i:03d}"
        template.x_nm = float(i * 20)
        template.y_nm = float(i * 20)
        template.status = "airborne"
        template.altitude_ft = 10000.0 + i
        world.aircraft[template.callsign] = template
    ac_list = list(world.aircraft.values())
    baseline_count = (n * (n - 1)) // 2
    pruned_count = sum(1 for _ in _candidate_pairs(ac_list, world.rules.min_horizontal_nm))
    assert pruned_count < baseline_count
