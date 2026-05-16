from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .models import AirportSnapshotRequest, FetchedTrafficWindow, FlightTrack, TrackPoint


class OpenSkyProvider:
    def __init__(self, username: str | None = None, password: str | None = None) -> None:
        self.username = username or os.getenv("OPENSKY_USERNAME")
        self.password = password or os.getenv("OPENSKY_PASSWORD")

    def fetch_tracks(self, request: AirportSnapshotRequest) -> FetchedTrafficWindow:
        raise NotImplementedError("Network-backed OpenSky fetching is intentionally not implemented in tests")

    def normalize_payload(self, request: AirportSnapshotRequest, payload: dict[str, Any]) -> FetchedTrafficWindow:
        tracks: list[FlightTrack] = []
        for flight in payload.get("flights", []):
            points = [
                TrackPoint(
                    timestamp=datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")),
                    lat=float(p["lat"]),
                    lon=float(p["lon"]),
                    altitude_ft=float(p.get("altitude_ft", 0.0)),
                    ground_speed_kt=float(p.get("ground_speed_kt", 0.0)),
                    track_deg=float(p.get("track_deg", 0.0)) % 360,
                    vertical_rate_fpm=float(p.get("vertical_rate_fpm", 0.0)),
                    on_ground=bool(p.get("on_ground", False)),
                )
                for p in flight.get("points", [])
            ]
            tracks.append(
                FlightTrack(
                    provider="opensky",
                    provider_ids={"flight_id": str(flight.get("id", ""))},
                    callsign=flight.get("callsign"),
                    icao24=flight.get("icao24"),
                    origin=flight.get("origin"),
                    destination=flight.get("destination"),
                    aircraft_type=flight.get("aircraft_type"),
                    points=points,
                    provider_metadata={"raw": flight.get("metadata", {})},
                )
            )
        return FetchedTrafficWindow(provider="opensky", request=request, tracks=tracks, fetched_at=datetime.now(timezone.utc))


class FlightRadar24Provider:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("FLIGHTRADAR24_API_KEY")

    def fetch_tracks(self, request: AirportSnapshotRequest) -> FetchedTrafficWindow:
        raise NotImplementedError("Network-backed FR24 fetching is intentionally not implemented in tests")

    def normalize_payload(self, request: AirportSnapshotRequest, payload: dict[str, Any]) -> FetchedTrafficWindow:
        tracks: list[FlightTrack] = []
        for flight in payload.get("data", []):
            points = [
                TrackPoint(
                    timestamp=datetime.fromisoformat(p["ts"].replace("Z", "+00:00")),
                    lat=float(p["lat"]),
                    lon=float(p["lon"]),
                    altitude_ft=float(p.get("alt_ft", 0.0)),
                    ground_speed_kt=float(p.get("gs_kt", 0.0)),
                    track_deg=float(p.get("hdg", 0.0)) % 360,
                    vertical_rate_fpm=float(p.get("vr_fpm", 0.0)),
                    on_ground=bool(p.get("gnd", False)),
                )
                for p in flight.get("track", [])
            ]
            tracks.append(
                FlightTrack(
                    provider="flightradar24",
                    provider_ids={"flight_id": str(flight.get("id", ""))},
                    callsign=flight.get("callsign"),
                    icao24=flight.get("icao24"),
                    origin=flight.get("origin"),
                    destination=flight.get("destination"),
                    aircraft_type=flight.get("aircraft_type"),
                    points=points,
                    provider_metadata={"raw": flight.get("meta", {})},
                )
            )
        return FetchedTrafficWindow(provider="flightradar24", request=request, tracks=tracks, fetched_at=datetime.now(timezone.utc))
