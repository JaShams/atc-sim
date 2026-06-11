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
    "hold_at_waypoint",
    "exit_hold",
    "resume_procedure",
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
    wake_category: str | None = None
    aircraft_type: str | None = None
    vertical_rate_fpm: float = 0.0
    status: str = "airborne"
    target_runway: str | None = None
    clearance: str | None = None
    target_altitude_ft: float | None = None
    target_heading_deg: float | None = None
    emergency: bool = False
    ready_time_sec: int | None = None
    takeoff_time_sec: int | None = None
    landing_time_sec: int | None = None
    ideal_takeoff_time_sec: int | None = None
    ideal_landing_time_sec: int | None = None
    emergency_subtype: str | None = None
    emergency_deadline_sec: int | None = None
    emergency_remaining_endurance_sec: int | None = None
    emergency_terminal_failure: bool = False
    emergency_require_return_to_land: bool = False
    max_climb_fpm: float | None = None
    max_descent_fpm: float | None = None
    max_speed_kt: float | None = None
    min_speed_kt: float | None = None
    max_turn_rate_deg_per_sec: float | None = None
    route_id: str | None = None
    procedure_type: str | None = None
    waypoints: list[dict[str, Any]] = field(default_factory=list)
    current_leg_index: int = 0
    current_leg_completed: bool = False
    managed_route_active: bool = True
    manual_override_until_sec: int | None = None
    hold_fix_id: str | None = None
    hold_fix_x_nm: float | None = None
    hold_fix_y_nm: float | None = None
    hold_leg_length_nm: float | None = None
    hold_turn_direction: str | None = None
    hold_altitude_ft: float | None = None
    hold_phase: str | None = None
    hold_leg_progress_nm: float = 0.0
    hold_turn_remaining_deg: float = 0.0


@dataclass
class AirportState:
    runway_id: str
    active_runway: str
    runway_occupied_by: str | None = None
    runway_phase: str | None = None
    runway_occupied_until_sec: int | None = None
    departure_queue: list[str] = field(default_factory=list)
    layout: dict[str, Any] | None = None
    reference_point: dict[str, float] | None = None
    display_center: dict[str, float] | None = None
    default_range_nm: float | None = None


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
    outcome_horizon_ticks: int = 3
    outcome_immediate_epsilon: float = 0.01
    outcome_window_epsilon: float = 0.02
    outcome_normalization_floor: float = 1.0
    debug_require_trigger_provenance: bool = False
    restricted_zones: list[dict[str, Any]] = field(default_factory=list)
    pilot_readback_delay_sec: dict[str, int] = field(default_factory=lambda: {"min": 0, "max": 0})
    command_delay_seed: int | None = None


@dataclass
class ScoringConfig:
    base_score: float = 100.0
    loss_of_separation_penalty: float = -20.0
    invalid_command_penalty: float = -5.0
    secondary_conflicts_created_penalty: float = -4.0
    conflicts_worsened_penalty: float = -3.0
    conflicts_delayed_reward: float = 0.5
    conflict_resolved_reward: float = 4.0
    arrival_delay_sec_penalty: float = -0.02
    departure_delay_sec_penalty: float = -0.02
    successful_landing_reward: float = 3.0
    successful_departure_reward: float = 2.0
    emergency_handled_reward: float = 8.0
    emergency_unhandled_penalty: float = -12.0
    emergency_priority_compliance_reward: float = 2.5
    emergency_priority_violation_penalty: float = -4.0
    restricted_zone_violation_penalty: float = -10.0
    runway_incursion_penalty: float = -15.0


@dataclass
class WorldState:
    time_sec: int
    tick_sec: int
    airport: AirportState
    weather: Weather
    rules: RulesConfig
    aircraft: dict[str, Aircraft]
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    events: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        airport = {
            "runway_id": self.airport.runway_id,
            "active_runway": self.airport.active_runway,
            "runway_occupied_by": self.airport.runway_occupied_by,
            "runway_phase": self.airport.runway_phase,
            "runway_occupied_until_sec": self.airport.runway_occupied_until_sec,
            "departure_queue": list(self.airport.departure_queue),
        }
        if self.airport.layout is not None:
            airport["layout"] = self.airport.layout
        if self.airport.reference_point is not None:
            airport["reference_point"] = dict(self.airport.reference_point)
        if self.airport.display_center is not None:
            airport["display_center"] = dict(self.airport.display_center)
        if self.airport.default_range_nm is not None:
            airport["default_range_nm"] = self.airport.default_range_nm
        return {
            "time_sec": self.time_sec,
            "airport": airport,
            "weather": self.weather.__dict__.copy(),
            "aircraft": {k: v.__dict__.copy() for k, v in self.aircraft.items()},
        }
