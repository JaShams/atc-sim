
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
    assert isinstance(preds, list)
    if preds:
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
