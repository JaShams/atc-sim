from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

ALLOWED_AIRCRAFT_ROLES = {"arrival", "departure"}
ALLOWED_AIRCRAFT_STATUSES = {
    "airborne",
    "on_final",
    "go_around",
    "landed",
    "waiting_departure",
    "departed",
    "rolling",
    "airborne_departure",
    "exited_airspace",
}
ALLOWED_EVENT_TYPES = {"wind_change", "emergency_declare"}
REQUIRED_TOP_LEVEL = {"airport", "aircraft"}
REQUIRED_AIRCRAFT = {"callsign", "role", "x_nm", "y_nm", "altitude_ft", "speed_kt", "heading_deg", "status"}
REQUIRED_AIRPORT = {"runway_id", "active_runway"}
NUMERIC_RULES = {
    "min_horizontal_nm",
    "min_vertical_ft",
    "min_altitude_ft",
    "min_speed_kt",
    "max_speed_kt",
    "lookahead_seconds",
    "runway_arrival_protection_nm",
}


class ScenarioValidationError(ValueError):
    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        joined = "\n".join(f"- {error}" for error in errors)
        super().__init__(f"Invalid scenario {path}:\n{joined}")


def validate_scenario_document(data: Any, path: Path | str = "<memory>") -> None:
    errors: list[str] = []
    scenario_path = Path(path)

    if not isinstance(data, Mapping):
        raise ScenarioValidationError(scenario_path, ["scenario must be a JSON object"])

    _require_keys(data, REQUIRED_TOP_LEVEL, "scenario", errors)
    _validate_tick(data, errors)
    _validate_airport(data.get("airport"), errors)
    callsigns = _validate_aircraft(data.get("aircraft"), errors)
    _validate_departure_queue(data.get("airport"), callsigns, errors)
    _validate_weather(data.get("weather", {}), errors)
    _validate_rules(data.get("rules", {}), errors)
    _validate_scoring(data.get("scoring", {}), errors)
    _validate_events(data.get("events", []), callsigns, errors)

    if errors:
        raise ScenarioValidationError(scenario_path, errors)


def _require_keys(value: Mapping[str, Any], required: set[str], label: str, errors: list[str]) -> None:
    missing = sorted(required - set(value))
    for key in missing:
        errors.append(f"{label} missing required field '{key}'")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_non_negative_number(value: Any) -> bool:
    return _is_number(value) and value >= 0


def _is_runway_id(value: Any) -> bool:
    if not isinstance(value, str) or not value.isdigit():
        return False
    n = int(value)
    return 1 <= n <= 36


def _validate_tick(data: Mapping[str, Any], errors: list[str]) -> None:
    tick_sec = data.get("tick_sec", 5)
    if not isinstance(tick_sec, int) or tick_sec <= 0:
        errors.append("tick_sec must be a positive integer")


def _validate_airport(airport: Any, errors: list[str]) -> None:
    if not isinstance(airport, Mapping):
        errors.append("airport must be an object")
        return
    _require_keys(airport, REQUIRED_AIRPORT, "airport", errors)
    for field in ("runway_id", "active_runway"):
        if field in airport and not _is_runway_id(airport[field]):
            errors.append(f"airport.{field} must be a runway number string from '01' to '36'")
    if "departure_queue" in airport:
        queue = airport["departure_queue"]
        if not isinstance(queue, list) or not all(isinstance(callsign, str) and callsign for callsign in queue):
            errors.append("airport.departure_queue must be a list of callsign strings")
    if airport.get("runway_occupied_until_sec") is not None and not _is_non_negative_number(airport.get("runway_occupied_until_sec")):
        errors.append("airport.runway_occupied_until_sec must be null or non-negative")
    if "layout" in airport:
        _validate_airport_layout(airport["layout"], errors)


def _validate_id(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.id must be a non-empty string")


def _validate_point(point: Any, label: str, errors: list[str]) -> None:
    if not isinstance(point, Mapping):
        errors.append(f"{label} must be an object")
        return
    for field in ("x_nm", "y_nm"):
        if field not in point:
            errors.append(f"{label} missing required field '{field}'")
        elif not _is_number(point[field]):
            errors.append(f"{label}.{field} must be numeric")


def _validate_point_list(points: Any, label: str, minimum: int, errors: list[str], *, exact: bool = False) -> None:
    if not isinstance(points, list):
        errors.append(f"{label} must be a list")
        return
    if exact and len(points) != minimum:
        errors.append(f"{label} must contain exactly {minimum} points")
    elif not exact and len(points) < minimum:
        errors.append(f"{label} must contain at least {minimum} points")
    for idx, point in enumerate(points):
        _validate_point(point, f"{label}[{idx}]", errors)


def _validate_optional_width(surface: Mapping[str, Any], label: str, errors: list[str]) -> None:
    if "width_nm" in surface and not _is_non_negative_number(surface["width_nm"]):
        errors.append(f"{label}.width_nm must be non-negative")


def _validate_airport_layout(layout: Any, errors: list[str]) -> None:
    if not isinstance(layout, Mapping):
        errors.append("airport.layout must be an object")
        return

    runways = layout.get("runways", [])
    if not isinstance(runways, list):
        errors.append("airport.layout.runways must be a list when present")
    else:
        for idx, runway in enumerate(runways):
            label = f"airport.layout.runways[{idx}]"
            if not isinstance(runway, Mapping):
                errors.append(f"{label} must be an object")
                continue
            _validate_id(runway.get("id"), label, errors)
            _validate_point_list(runway.get("ends"), f"{label}.ends", 2, errors, exact=True)
            _validate_optional_width(runway, label, errors)

    taxiways = layout.get("taxiways", [])
    if not isinstance(taxiways, list):
        errors.append("airport.layout.taxiways must be a list when present")
    else:
        for idx, taxiway in enumerate(taxiways):
            label = f"airport.layout.taxiways[{idx}]"
            if not isinstance(taxiway, Mapping):
                errors.append(f"{label} must be an object")
                continue
            _validate_id(taxiway.get("id"), label, errors)
            _validate_point_list(taxiway.get("points"), f"{label}.points", 2, errors)
            _validate_optional_width(taxiway, label, errors)

    aprons = layout.get("aprons", [])
    if not isinstance(aprons, list):
        errors.append("airport.layout.aprons must be a list when present")
    else:
        for idx, apron in enumerate(aprons):
            label = f"airport.layout.aprons[{idx}]"
            if not isinstance(apron, Mapping):
                errors.append(f"{label} must be an object")
                continue
            _validate_id(apron.get("id"), label, errors)
            _validate_point_list(apron.get("polygon"), f"{label}.polygon", 3, errors)

    stands = layout.get("stands", [])
    if not isinstance(stands, list):
        errors.append("airport.layout.stands must be a list when present")
    else:
        for idx, stand in enumerate(stands):
            label = f"airport.layout.stands[{idx}]"
            if not isinstance(stand, Mapping):
                errors.append(f"{label} must be an object")
                continue
            _validate_id(stand.get("id"), label, errors)
            _validate_point(stand.get("position"), f"{label}.position", errors)


def _validate_aircraft(aircraft: Any, errors: list[str]) -> set[str]:
    callsigns: set[str] = set()
    if not isinstance(aircraft, list) or not aircraft:
        errors.append("aircraft must be a non-empty list")
        return callsigns

    for idx, ac in enumerate(aircraft):
        label = f"aircraft[{idx}]"
        if not isinstance(ac, Mapping):
            errors.append(f"{label} must be an object")
            continue
        _require_keys(ac, REQUIRED_AIRCRAFT, label, errors)
        callsign = ac.get("callsign")
        if not isinstance(callsign, str) or not callsign.strip():
            errors.append(f"{label}.callsign must be a non-empty string")
        elif callsign in callsigns:
            errors.append(f"{label}.callsign duplicates '{callsign}'")
        else:
            callsigns.add(callsign)
        if ac.get("role") not in ALLOWED_AIRCRAFT_ROLES:
            errors.append(f"{label}.role must be one of {sorted(ALLOWED_AIRCRAFT_ROLES)}")
        if ac.get("status") not in ALLOWED_AIRCRAFT_STATUSES:
            errors.append(f"{label}.status must be one of {sorted(ALLOWED_AIRCRAFT_STATUSES)}")
        for field in ("x_nm", "y_nm", "altitude_ft", "speed_kt", "heading_deg"):
            if field in ac and not _is_number(ac[field]):
                errors.append(f"{label}.{field} must be numeric")
        if _is_number(ac.get("altitude_ft")) and ac["altitude_ft"] < 0:
            errors.append(f"{label}.altitude_ft must be non-negative")
        if _is_number(ac.get("speed_kt")) and ac["speed_kt"] < 0:
            errors.append(f"{label}.speed_kt must be non-negative")
        if _is_number(ac.get("heading_deg")) and not 0 <= ac["heading_deg"] <= 359:
            errors.append(f"{label}.heading_deg must be in [0, 359]")
        if ac.get("target_runway") is not None and not _is_runway_id(ac.get("target_runway")):
            errors.append(f"{label}.target_runway must be null or a runway number string from '01' to '36'")
    return callsigns


def _validate_departure_queue(airport: Any, callsigns: set[str], errors: list[str]) -> None:
    if not isinstance(airport, Mapping):
        return
    queue = airport.get("departure_queue", [])
    if isinstance(queue, list):
        for callsign in queue:
            if callsign not in callsigns:
                errors.append(f"airport.departure_queue references unknown aircraft '{callsign}'")


def _validate_weather(weather: Any, errors: list[str]) -> None:
    if not isinstance(weather, Mapping):
        errors.append("weather must be an object when present")
        return
    for field in ("wind_dir_deg", "wind_speed_kt", "visibility_sm"):
        if field in weather and not _is_number(weather[field]):
            errors.append(f"weather.{field} must be numeric")
    if "wind_dir_deg" in weather and _is_number(weather["wind_dir_deg"]) and not 0 <= weather["wind_dir_deg"] <= 359:
        errors.append("weather.wind_dir_deg must be in [0, 359]")
    if "wind_speed_kt" in weather and _is_number(weather["wind_speed_kt"]) and weather["wind_speed_kt"] < 0:
        errors.append("weather.wind_speed_kt must be non-negative")


def _validate_rules(rules: Any, errors: list[str]) -> None:
    if not isinstance(rules, Mapping):
        errors.append("rules must be an object when present")
        return
    for field in NUMERIC_RULES:
        if field in rules and not _is_number(rules[field]):
            errors.append(f"rules.{field} must be numeric")
    if _is_number(rules.get("min_speed_kt")) and _is_number(rules.get("max_speed_kt")) and rules["min_speed_kt"] > rules["max_speed_kt"]:
        errors.append("rules.min_speed_kt must be <= rules.max_speed_kt")


def _validate_scoring(scoring: Any, errors: list[str]) -> None:
    if not isinstance(scoring, Mapping):
        errors.append("scoring must be an object when present")
        return
    for field, value in scoring.items():
        if not _is_number(value):
            errors.append(f"scoring.{field} must be numeric")


def _validate_events(events: Any, callsigns: set[str], errors: list[str]) -> None:
    if not isinstance(events, list):
        errors.append("events must be a list when present")
        return
    for idx, event in enumerate(events):
        label = f"events[{idx}]"
        if not isinstance(event, Mapping):
            errors.append(f"{label} must be an object")
            continue
        time_sec = event.get("time_sec")
        if not isinstance(time_sec, int) or time_sec < 0:
            errors.append(f"{label}.time_sec must be a non-negative integer")
        etype = event.get("type")
        if etype not in ALLOWED_EVENT_TYPES:
            errors.append(f"{label}.type must be one of {sorted(ALLOWED_EVENT_TYPES)}")
        if etype == "wind_change":
            for field in ("wind_dir_deg", "wind_speed_kt"):
                if not _is_number(event.get(field)):
                    errors.append(f"{label}.{field} is required and must be numeric")
            if "active_runway" in event and not _is_runway_id(event["active_runway"]):
                errors.append(f"{label}.active_runway must be a runway number string from '01' to '36'")
        if etype == "emergency_declare" and event.get("aircraft") not in callsigns:
            errors.append(f"{label}.aircraft must reference a known callsign")
