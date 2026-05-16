from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class TrackPoint:
    timestamp: datetime
    lat: float
    lon: float
    altitude_ft: float
    ground_speed_kt: float
    track_deg: float
    vertical_rate_fpm: float
    on_ground: bool


@dataclass(frozen=True)
class FlightTrack:
    provider: str
    provider_ids: dict[str, str]
    callsign: str | None
    icao24: str | None
    origin: str | None
    destination: str | None
    aircraft_type: str | None
    points: list[TrackPoint]
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AirportSnapshotRequest:
    airport_icao: str
    airport_lat: float
    airport_lon: float
    runway_id: str
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class FetchedTrafficWindow:
    provider: str
    request: AirportSnapshotRequest
    tracks: list[FlightTrack]
    fetched_at: datetime
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioSeedConfig:
    runway_id: str
    active_runway: str
    tick_sec: int = 5
    default_target_runway: str | None = None
