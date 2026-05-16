from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from atc_benchmark.ingest.adapters import FlightRadar24Provider, OpenSkyProvider
from atc_benchmark.ingest.models import AirportSnapshotRequest, ScenarioSeedConfig
from atc_benchmark.ingest.scenario_builder import build_scenario
from atc_benchmark.simulator.scenario_validation import validate_scenario_document


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest provider traffic and generate scenario JSON")
    p.add_argument("--provider", choices=["opensky", "flightradar24"], required=True)
    p.add_argument("--airport", required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--runway", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--output", required=True)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    request = AirportSnapshotRequest(
        airport_icao=args.airport,
        airport_lat=args.lat,
        airport_lon=args.lon,
        runway_id=args.runway,
        start_time=datetime.fromisoformat(args.start),
        end_time=datetime.fromisoformat(args.end),
    )
    provider = OpenSkyProvider() if args.provider == "opensky" else FlightRadar24Provider()
    window = provider.fetch_tracks(request)
    scenario = build_scenario(window, ScenarioSeedConfig(runway_id=args.runway, active_runway=args.runway))
    validate_scenario_document(scenario)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scenario, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
