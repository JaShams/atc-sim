from __future__ import annotations

from .conflict_detection import detect_conflicts
from .models import WorldState


def detect_decision_points(world: WorldState) -> list[dict]:
    out: list[dict] = []
    conflicts = detect_conflicts(world)
    for c in conflicts:
        out.append({"type": "predicted_conflict", **c})

    for ac in world.aircraft.values():
        if ac.status == "waiting_departure":
            out.append({"type": "departure_ready", "aircraft": [ac.callsign]})
        if ac.status == "on_final" and world.airport.runway_occupied_by:
            out.append({"type": "runway_occupied_on_final", "aircraft": [ac.callsign]})
        if ac.emergency and ac.status in {"airborne", "on_final"}:
            out.append({"type": "emergency", "aircraft": [ac.callsign]})
    return out
