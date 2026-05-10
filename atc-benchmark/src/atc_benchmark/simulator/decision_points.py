from __future__ import annotations

from .conflict_detection import detect_conflicts, predict_conflicts
from .models import WorldState


def detect_decision_points(world: WorldState) -> list[dict]:
    out: list[dict] = []

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
