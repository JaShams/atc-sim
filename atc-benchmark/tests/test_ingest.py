from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from atc_benchmark.ingest.adapters import FlightRadar24Provider, OpenSkyProvider
from atc_benchmark.ingest.models import AirportSnapshotRequest, ScenarioSeedConfig
from atc_benchmark.ingest.providers import TrafficDataProvider
from atc_benchmark.ingest.scenario_builder import build_scenario, latlon_to_local_nm
from atc_benchmark.simulator.scenario_validation import validate_scenario_document


def _request() -> AirportSnapshotRequest:
    return AirportSnapshotRequest(
        airport_icao="OMAA",
        airport_lat=24.433,
        airport_lon=54.651,
        runway_id="13",
        start_time=datetime.fromisoformat("2026-01-01T09:50:00+00:00"),
        end_time=datetime.fromisoformat("2026-01-01T10:10:00+00:00"),
    )


def test_provider_contract_typing() -> None:
    provider: TrafficDataProvider = OpenSkyProvider()
    assert hasattr(provider, "fetch_tracks")


def test_opensky_normalization_fixture() -> None:
    payload = json.loads(Path("tests/fixtures/opensky_sample.json").read_text())
    window = OpenSkyProvider().normalize_payload(_request(), payload)
    assert window.provider == "opensky"
    assert window.tracks[0].callsign == "UAE101"


def test_fr24_normalization_fixture() -> None:
    payload = json.loads(Path("tests/fixtures/fr24_sample.json").read_text())
    window = FlightRadar24Provider().normalize_payload(_request(), payload)
    assert window.provider == "flightradar24"
    assert window.tracks[0].points[0].on_ground is True


def test_latlon_local_conversion() -> None:
    x, y = latlon_to_local_nm(24.443, 54.661, 24.433, 54.651)
    assert x > 0
    assert y > 0


def test_scenario_build_and_validate() -> None:
    payload = json.loads(Path("tests/fixtures/opensky_sample.json").read_text())
    window = OpenSkyProvider().normalize_payload(_request(), payload)
    scenario = build_scenario(window, ScenarioSeedConfig(runway_id="13", active_runway="13"))
    assert scenario["airport"]["runway_id"] == "13"
    assert isinstance(scenario["aircraft"][0]["speed_kt"], float)
    assert "aircraft_type" in scenario["aircraft"][0]
    assert "wake_category" in scenario["aircraft"][0]
    validate_scenario_document(scenario)
