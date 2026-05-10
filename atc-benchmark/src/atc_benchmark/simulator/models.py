from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ALLOWED_ACTION_TYPES = {
    "assign_heading",
    "assign_altitude",
    "assign_speed",
    "clear_to_land",
    "clear_for_takeoff",
    "go_around",
    "hold_short",
    "hold_position",
    "continue_approach",
    "no_op",
}


@dataclass
class Aircraft:
    callsign: str
    role: str
    x_nm: float
    y_nm: float
    altitude_ft: float
    speed_kt: float
    heading_deg: float
    vertical_rate_fpm: float = 0.0
    status: str = "airborne"
    target_runway: str | None = None
    clearance: str | None = None
    emergency: bool = False
    ready_time_sec: int | None = None
    takeoff_time_sec: int | None = None
    landing_time_sec: int | None = None
    ideal_takeoff_time_sec: int | None = None
    ideal_landing_time_sec: int | None = None


@dataclass
class AirportState:
    runway_id: str
    active_runway: str
    runway_occupied_by: str | None = None
    departure_queue: list[str] = field(default_factory=list)


@dataclass
class Weather:
    wind_dir_deg: int = 0
    wind_speed_kt: int = 0
    visibility_sm: float = 10.0


@dataclass
class RulesConfig:
    min_horizontal_nm: float = 3.0
    min_vertical_ft: float = 1000.0
    min_altitude_ft: float = 1000.0
    min_speed_kt: float = 120.0
    max_speed_kt: float = 280.0
    lookahead_seconds: int = 60
    runway_arrival_protection_nm: float = 5.0


@dataclass
class WorldState:
    time_sec: int
    tick_sec: int
    airport: AirportState
    weather: Weather
    rules: RulesConfig
    aircraft: dict[str, Aircraft]
    events: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "time_sec": self.time_sec,
            "airport": {
                "runway_id": self.airport.runway_id,
                "active_runway": self.airport.active_runway,
                "runway_occupied_by": self.airport.runway_occupied_by,
                "departure_queue": list(self.airport.departure_queue),
            },
            "weather": self.weather.__dict__.copy(),
            "aircraft": {k: v.__dict__.copy() for k, v in self.aircraft.items()},
        }
