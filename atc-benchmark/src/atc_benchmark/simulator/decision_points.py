from __future__ import annotations

from .conflict_detection import detect_conflicts, predict_conflicts
from .models import WorldState


def _runway_heading_deg(runway_id: str) -> float:
    n = int(runway_id)
    return 360.0 if n == 36 else float(n * 10)


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def detect_decision_points(world: WorldState) -> list[dict]:
    out: list[dict] = []

    runway_heading = _runway_heading_deg(world.airport.active_runway)
    wind_alignment_delta = _angle_delta_deg(world.weather.wind_dir_deg, runway_heading)
    if wind_alignment_delta >= 45:
        impacted = [ac.callsign for ac in world.aircraft.values() if ac.status in {"waiting_departure", "on_final", "airborne"}]
        out.append({
            "type": "wind_runway_mismatch",
            "severity": "critical" if wind_alignment_delta >= 60 else "advisory",
            "wind_alignment_delta_deg": wind_alignment_delta,
            "aircraft": impacted,
            "active_runway": world.airport.active_runway,
        })

    for c in detect_conflicts(world):
        out.append({"type": "active_conflict", **c})
    for c in predict_conflicts(world):
        out.append(c)

    for ac in world.aircraft.values():
        if ac.status == "waiting_departure":
            out.append({"type": "departure_ready", "aircraft": [ac.callsign]})
        if ac.status == "on_final" and world.airport.runway_occupied_by:
            out.append({"type": "runway_occupied_on_final", "aircraft": [ac.callsign]})
        if ac.emergency and ac.status in {"airborne", "on_final"}:
            out.append({"type": "emergency", "aircraft": [ac.callsign]})
    return out
