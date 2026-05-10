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
