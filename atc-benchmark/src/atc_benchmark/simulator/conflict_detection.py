from __future__ import annotations

from itertools import combinations
from math import hypot

from .models import WorldState


def horizontal_distance_nm(a, b) -> float:
    return hypot(a.x_nm - b.x_nm, a.y_nm - b.y_nm)


def detect_conflicts(world: WorldState) -> list[dict]:
    conflicts: list[dict] = []
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final"}]
    for a, b in combinations(ac_list, 2):
        h = horizontal_distance_nm(a, b)
        v = abs(a.altitude_ft - b.altitude_ft)
        if h < world.rules.min_horizontal_nm and v < world.rules.min_vertical_ft:
            conflicts.append({"type": "loss_of_separation", "aircraft": [a.callsign, b.callsign], "horizontal_nm": h, "vertical_ft": v})
    return conflicts
