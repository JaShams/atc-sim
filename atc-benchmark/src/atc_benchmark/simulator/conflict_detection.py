from __future__ import annotations

from itertools import combinations
from math import cos, hypot, radians, sin
from typing import Any, Mapping

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




def _runway_lookup(layout: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(layout, Mapping):
        return {}
    runways = layout.get("runways")
    if not isinstance(runways, list):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for runway in runways:
        if isinstance(runway, Mapping) and isinstance(runway.get("id"), str):
            out[runway["id"]] = runway
    return out


def _distance_to_centerline_nm(ac, centerline: Mapping[str, Any]) -> float | None:
    start = centerline.get("start")
    end = centerline.get("end")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        return None
    try:
        x1, y1 = float(start["x_nm"]), float(start["y_nm"])
        x2, y2 = float(end["x_nm"]), float(end["y_nm"])
    except (KeyError, TypeError, ValueError):
        return None
    dx, dy = x2 - x1, y2 - y1
    denom = hypot(dx, dy)
    if denom <= 0:
        return None
    return abs(dy * ac.x_nm - dx * ac.y_nm + x2 * y1 - y2 * x1) / denom


def _is_in_final_approach_envelope(ac, runway: Mapping[str, Any]) -> bool:
    envelope = runway.get("final_approach_envelope")
    if not isinstance(envelope, Mapping):
        return False
    max_distance_nm = envelope.get("max_distance_nm")
    min_altitude_ft = envelope.get("min_altitude_ft", 0.0)
    max_altitude_ft = envelope.get("max_altitude_ft", 5000.0)
    if not isinstance(max_distance_nm, (int, float)):
        return False
    if ac.target_runway != runway.get("id"):
        return False
    return hypot(ac.x_nm, ac.y_nm) <= float(max_distance_nm) and float(min_altitude_ft) <= ac.altitude_ft <= float(max_altitude_ft)


def _established_on_parallel_ils(a, b, world: WorldState) -> bool:
    layout = world.airport.layout
    if not isinstance(layout, Mapping):
        return False
    runway_by_id = _runway_lookup(layout)
    pairs = layout.get("parallel_runway_pairs")
    if not isinstance(pairs, list):
        return False
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        rwy_a = runway_by_id.get(pair.get("runway_a"))
        rwy_b = runway_by_id.get(pair.get("runway_b"))
        if not rwy_a or not rwy_b:
            continue
        if not (_is_in_final_approach_envelope(a, rwy_a) and _is_in_final_approach_envelope(b, rwy_b)) and not (_is_in_final_approach_envelope(a, rwy_b) and _is_in_final_approach_envelope(b, rwy_a)):
            continue
        ils_a = rwy_a.get("ils_centerline")
        ils_b = rwy_b.get("ils_centerline")
        if not isinstance(ils_a, Mapping) or not isinstance(ils_b, Mapping):
            continue
        tol = float(pair.get("established_tolerance_nm", 0.2))

        d_aa = _distance_to_centerline_nm(a, ils_a)
        d_ab = _distance_to_centerline_nm(a, ils_b)
        d_ba = _distance_to_centerline_nm(b, ils_a)
        d_bb = _distance_to_centerline_nm(b, ils_b)
        if None in {d_aa, d_ab, d_ba, d_bb}:
            continue
        if d_aa <= tol and d_bb <= tol and d_ab > tol and d_ba > tol:
            return True
        if d_ab <= tol and d_ba <= tol and d_aa > tol and d_bb > tol:
            return True
    return False

def _conflict_pair_id(a_callsign: str, b_callsign: str) -> str:
    low, high = sorted((a_callsign, b_callsign))
    return f"{low}|{high}"


def detect_conflicts(world: WorldState) -> list[dict]:
    conflicts: list[dict] = []
    ac_list = [a for a in world.aircraft.values() if a.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}]
    for a, b in combinations(ac_list, 2):
        h = horizontal_distance_nm(a, b)
        v = abs(a.altitude_ft - b.altitude_ft)
        min_horizontal_nm = world.rules.min_horizontal_nm
        if _established_on_parallel_ils(a, b, world):
            min_horizontal_nm = 0.0
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
            min_horizontal_nm = world.rules.min_horizontal_nm
            if _established_on_parallel_ils(a, b, world):
                min_horizontal_nm = 0.0
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
                    "vertical_ft": first_v,
                }
            )
    return predictions
