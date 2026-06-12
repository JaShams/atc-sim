from __future__ import annotations

from math import hypot

from .conflict_detection import detect_conflicts, predict_conflicts
from .models import WorldState
from .validator import _alignment_along_final_nm, _is_too_high_for_approach


def _runway_heading_deg(runway_id: str) -> float:
    n = int(runway_id)
    return 360.0 if n == 36 else float(n * 10)


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def detect_decision_points(
    world: WorldState,
    conflicts: list[dict] | None = None,
    predictions: list[dict] | None = None,
) -> list[dict]:
    """Detect controller decision points.

    ``conflicts``/``predictions`` accept precomputed results from
    detect_conflicts/predict_conflicts so callers that already ran them this
    tick do not pay for a second pass.
    """
    out: list[dict] = []

    runway_heading = _runway_heading_deg(world.airport.active_runway)
    wind_alignment_delta = _angle_delta_deg(world.weather.wind_dir_deg, runway_heading)
    # Calm wind favors no runway; direction is meaningless below ~5 kt.
    if world.weather.wind_speed_kt >= 5 and wind_alignment_delta >= 45:
        impacted = [ac.callsign for ac in world.aircraft.values() if ac.status in {"waiting_departure", "on_final", "airborne"}]
        out.append({
            "type": "wind_runway_mismatch",
            "severity": "critical" if wind_alignment_delta >= 60 else "advisory",
            "wind_alignment_delta_deg": wind_alignment_delta,
            "aircraft": impacted,
            "active_runway": world.airport.active_runway,
        })

    for c in detect_conflicts(world) if conflicts is None else conflicts:
        out.append({"type": "active_conflict", **c})
    for c in predict_conflicts(world) if predictions is None else predictions:
        out.append(dict(c))

    for ac in world.aircraft.values():
        if ac.status == "waiting_departure":
            out.append({"type": "departure_ready", "aircraft": [ac.callsign]})
        if ac.status == "on_final" and world.airport.runway_occupied_by:
            out.append({"type": "runway_occupied_on_final", "aircraft": [ac.callsign]})
        if ac.emergency and ac.status in {"airborne", "on_final"}:
            out.append({"type": "emergency", "aircraft": [ac.callsign]})
        if (
            ac.role == "arrival"
            and ac.status in {"airborne", "on_final"}
            and ac.clearance != "cleared_to_land"
            and world.airport.runway_occupied_by is None
        ):
            along_final_nm = _alignment_along_final_nm(world, ac)
            if along_final_nm is not None and not _is_too_high_for_approach(world, ac, along_final_nm):
                out.append({"type": "landing_clearance_available", "aircraft": [ac.callsign]})
        if (
            ac.role == "departure"
            and ac.status == "airborne_departure"
            and not ac.handed_off
            and hypot(ac.x_nm, ac.y_nm) >= world.rules.handoff_min_distance_nm
        ):
            out.append({"type": "handoff_due", "aircraft": [ac.callsign]})
    return out
