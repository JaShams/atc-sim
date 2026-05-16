import copy
import json

import pytest

from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.engine import load_world
from atc_benchmark.simulator.scenario_validation import ScenarioValidationError, validate_scenario_document


def _valid_payload():
    return json.loads(resolve_scenario_path("scenarios/crossing_conflict_001.json").read_text())


def test_valid_scenario_document_passes():
    validate_scenario_document(_valid_payload())


def test_valid_airport_layout_passes():
    payload = _valid_payload()
    payload["airport"]["layout"] = {
        "runways": [
            {
                "id": "09",
                "ends": [{"x_nm": -2.5, "y_nm": 0.0}, {"x_nm": 2.5, "y_nm": 0.0}],
                "width_nm": 0.08,
                "ils_centerline": {"start": {"x_nm": 0.0, "y_nm": -10.0}, "end": {"x_nm": 0.0, "y_nm": 0.0}},
                "final_approach_envelope": {"max_distance_nm": 10.0, "min_altitude_ft": 1500, "max_altitude_ft": 5000},
            },
            {"id": "10", "ends": [{"x_nm": 0.3, "y_nm": -2.5}, {"x_nm": 0.3, "y_nm": 2.5}], "ils_centerline": {"start": {"x_nm": 0.3, "y_nm": -10.0}, "end": {"x_nm": 0.3, "y_nm": 0.0}}, "final_approach_envelope": {"max_distance_nm": 10.0}}
        ],
        "parallel_runway_pairs": [{"runway_a": "09", "runway_b": "10", "established_tolerance_nm": 0.15}],
        "taxiways": [
            {
                "id": "A",
                "points": [{"x_nm": -1.0, "y_nm": -0.4}, {"x_nm": 1.0, "y_nm": -0.4}],
                "width_nm": 0.04,
            }
        ],
        "aprons": [
            {
                "id": "MAIN",
                "polygon": [
                    {"x_nm": -0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.5},
                ],
            }
        ],
        "stands": [{"id": "S1", "position": {"x_nm": -0.4, "y_nm": -0.7}}],
    }
    validate_scenario_document(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["aircraft"][0].update({"status": "teleporting"}), "status"),
        (lambda p: p["aircraft"][0].pop("callsign"), "callsign"),
        (lambda p: p["aircraft"][0].update({"heading_deg": 999}), "heading_deg"),
        (lambda p: p["airport"]["departure_queue"].append("MISSING"), "departure_queue"),
        (lambda p: p.setdefault("events", []).append({"time_sec": 5, "type": "emergency_declare", "aircraft": "MISSING"}), "events"),
    ],
)
def test_invalid_scenario_document_reports_errors(mutate, message):
    payload = copy.deepcopy(_valid_payload())
    mutate(payload)
    with pytest.raises(ScenarioValidationError) as exc:
        validate_scenario_document(payload)
    assert message in str(exc.value)


def test_load_world_validates_before_construction(tmp_path):
    payload = _valid_payload()
    payload["airport"]["active_runway"] = "99"
    scenario = tmp_path / "bad.json"
    scenario.write_text(json.dumps(payload))
    with pytest.raises(ScenarioValidationError):
        load_world(scenario)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["airport"]["layout"]["runways"][0]["ends"][0].update({"x_nm": "west"}), "x_nm"),
        (lambda p: p["airport"]["layout"]["runways"][0]["ends"].append({"x_nm": 3.0, "y_nm": 0.0}), "exactly 2"),
        (lambda p: p["airport"]["layout"]["taxiways"][0].update({"points": [{"x_nm": 0.0, "y_nm": 0.0}]}), "at least 2"),
        (lambda p: p["airport"]["layout"]["aprons"][0].update({"polygon": [{"x_nm": 0.0, "y_nm": 0.0}, {"x_nm": 1.0, "y_nm": 0.0}]}), "at least 3"),
    ],
)
def test_invalid_airport_layout_reports_errors(mutate, message):
    payload = _valid_payload()
    payload["airport"]["layout"] = {
        "runways": [{"id": "09", "ends": [{"x_nm": -2.5, "y_nm": 0.0}, {"x_nm": 2.5, "y_nm": 0.0}]}],
        "taxiways": [{"id": "A", "points": [{"x_nm": -1.0, "y_nm": -0.4}, {"x_nm": 1.0, "y_nm": -0.4}]}],
        "aprons": [
            {
                "id": "MAIN",
                "polygon": [
                    {"x_nm": -0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.9},
                    {"x_nm": 0.8, "y_nm": -0.5},
                ],
            }
        ],
        "stands": [{"id": "S1", "position": {"x_nm": -0.4, "y_nm": -0.7}}],
    }
    mutate(payload)
    with pytest.raises(ScenarioValidationError) as exc:
        validate_scenario_document(payload)
    assert message in str(exc.value)
