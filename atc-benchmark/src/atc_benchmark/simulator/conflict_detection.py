from __future__ import annotations

from itertools import combinations
from math import cos, hypot, radians, sin

from .models import WorldState


def horizontal_distance_nm(a, b) -> float:
    return hypot(a.x_nm - b.x_nm, a.y_nm - b.y_nm)


def _project_aircraft(ac, seconds: int, wind_dir_deg: float = 0.0, wind_speed_kt: float = 0.0) -> tuple[float, float, float]:
    dt_hr = seconds / 3600
    rad = radians(ac.heading_deg)
    wind_to_deg = (wind_dir_deg + 180) % 360
    wind_rad = radians(wind_to_deg)
    x = ac.x_nm + (sin(rad) * ac.speed_kt + sin(wind_rad) * wind_speed_kt) * dt_hr
    y = ac.y_nm + (cos(rad) * ac.speed_kt + cos(wind_rad) * wind_speed_kt) * dt_hr
    alt = ac.altitude_ft + ac.vertical_rate_fpm * (seconds / 60)
    return x, y, alt


def _conflict_pair_id(a_callsign: str, b_callsign: str) -> str:
    low, high = sorted((a_callsign, b_callsign))
    return f"{low}|{high}"


_WAKE_SIZE_RANK = {"light": 1, "medium": 2, "heavy": 3, "super": 4}


def _size_rank(ac) -> int:
    category = (ac.wake_category or "").strip().lower()
    return _WAKE_SIZE_RANK.get(category, 2)


def _requires_extended_trailing_minimum_nm(leader, follower) -> float | None:
    leader_type = (leader.aircraft_type or "").strip().upper()
    if leader_type not in {"B777", "A350", "A380"}:
        return None
    if _size_rank(follower) >= _size_rank(leader):
        return None
    return 7.0 if _size_rank(follower) <= _WAKE_SIZE_RANK["light"] else 5.0


def _is_trailing(leader, follower) -> bool:
    heading = radians(leader.heading_deg)
    rel_x = follower.x_nm - leader.x_nm
    rel_y = follower.y_nm - leader.y_nm
    along = rel_x * sin(heading) + rel_y * cos(heading)
    return along < 0


def _pair_horizontal_minimum_nm(a, b, base_min_nm: float) -> float:
    required = base_min_nm
    if _is_trailing(a, b):
        required = max(required, _requires_extended_trailing_minimum_nm(a, b) or base_min_nm)
    if _is_trailing(b, a):
        required = max(required, _requires_extended_trailing_minimum_nm(b, a) or base_min_nm)
    return required


def detect_conflicts(world: WorldState) -> list[dict]:
    conflicts: list[dict] = []
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}]
    for a, b in combinations(ac_list, 2):
        h = horizontal_distance_nm(a, b)
        v = abs(a.altitude_ft - b.altitude_ft)
        min_horizontal_nm = _pair_horizontal_minimum_nm(a, b, world.rules.min_horizontal_nm)
        if h < min_horizontal_nm and v < world.rules.min_vertical_ft:
            pair_id = _conflict_pair_id(a.callsign, b.callsign)
            conflicts.append(
                {
                    "type": "loss_of_separation",
                    "id": f"{pair_id}|{world.time_sec}",
                    "conflict_pair_id": pair_id,
                    "conflict_instance_id": f"{pair_id}|{world.time_sec}",
                    "aircraft": [a.callsign, b.callsign],
                    "horizontal_nm": h,
                    "required_horizontal_nm": min_horizontal_nm,
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
            ax, ay, aa = _project_aircraft(a, t, world.weather.wind_dir_deg, world.weather.wind_speed_kt)
            bx, by, ba = _project_aircraft(b, t, world.weather.wind_dir_deg, world.weather.wind_speed_kt)
            h = hypot(ax - bx, ay - by)
            v = abs(aa - ba)
            min_horizontal_nm = _pair_horizontal_minimum_nm(a, b, world.rules.min_horizontal_nm)
            if h < min_horizontal_nm and v < world.rules.min_vertical_ft:
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
                    "required_horizontal_nm": min_horizontal_nm,
                    "vertical_ft": first_v,
                }
            )
    return predictions
