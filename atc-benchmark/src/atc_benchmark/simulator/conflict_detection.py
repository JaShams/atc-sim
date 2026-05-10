from __future__ import annotations

from itertools import combinations
from math import cos, hypot, radians, sin

from .models import WorldState


def horizontal_distance_nm(a, b) -> float:
    return hypot(a.x_nm - b.x_nm, a.y_nm - b.y_nm)


def _project_aircraft(ac, seconds: int) -> tuple[float, float, float]:
    dt_hr = seconds / 3600
    rad = radians(ac.heading_deg)
    x = ac.x_nm + sin(rad) * ac.speed_kt * dt_hr
    y = ac.y_nm + cos(rad) * ac.speed_kt * dt_hr
    alt = ac.altitude_ft + ac.vertical_rate_fpm * (seconds / 60)
    return x, y, alt


def _conflict_pair_id(a_callsign: str, b_callsign: str) -> str:
    low, high = sorted((a_callsign, b_callsign))
    return f"{low}|{high}"


def detect_conflicts(world: WorldState) -> list[dict]:
    conflicts: list[dict] = []
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}]
    for a, b in combinations(ac_list, 2):
        h = horizontal_distance_nm(a, b)
        v = abs(a.altitude_ft - b.altitude_ft)
        if h < world.rules.min_horizontal_nm and v < world.rules.min_vertical_ft:
            pair_id = _conflict_pair_id(a.callsign, b.callsign)
            conflicts.append(
                {
                    "type": "loss_of_separation",
                    "id": f"{pair_id}|{world.time_sec}",
                    "conflict_pair_id": pair_id,
                    "conflict_instance_id": f"{pair_id}|{world.time_sec}",
                    "aircraft": [a.callsign, b.callsign],
                    "horizontal_nm": h,
                    "vertical_ft": v,
                }
            )
    return conflicts


def predict_conflicts(world: WorldState) -> list[dict]:
    predictions: list[dict] = []
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}]
    step = max(world.tick_sec, 1)
    for a, b in combinations(ac_list, 2):
        first_conflict_time = None
        first_h = None
        first_v = None
        for t in range(step, world.rules.lookahead_seconds + step, step):
            ax, ay, aa = _project_aircraft(a, t)
            bx, by, ba = _project_aircraft(b, t)
            h = hypot(ax - bx, ay - by)
            v = abs(aa - ba)
            if h < world.rules.min_horizontal_nm and v < world.rules.min_vertical_ft:
                first_conflict_time = t
                first_h = h
                first_v = v
                break
        if first_conflict_time is not None:
            abs_time = world.time_sec + first_conflict_time
            predictions.append(
                {
                    "type": "predicted_conflict",
                    "id": f"{_conflict_pair_id(a.callsign, b.callsign)}|{abs_time}",
                    "conflict_pair_id": _conflict_pair_id(a.callsign, b.callsign),
                    "conflict_instance_id": f"{_conflict_pair_id(a.callsign, b.callsign)}|{abs_time}",
                    "aircraft": [a.callsign, b.callsign],
                    "in_seconds": first_conflict_time,
                    "predicted_time_sec": abs_time,
                    "horizontal_nm": first_h,
                    "vertical_ft": first_v,
                }
            )
    return predictions
