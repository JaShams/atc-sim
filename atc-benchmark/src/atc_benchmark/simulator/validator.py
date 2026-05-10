from __future__ import annotations

from math import cos, hypot, radians, sin

from .models import ALLOWED_ACTION_TYPES, WorldState

TAKEOFF_RUNWAY_OCCUPANCY_SEC = 35
LANDING_RUNWAY_OCCUPANCY_SEC = 50


def _runway_heading_deg(active_runway: str) -> float:
    return (int(active_runway) % 36) * 10


def _closest_inbound_arrival_final_distance(world: WorldState) -> float | None:
    dists = []
    rw_rad = radians(_runway_heading_deg(world.airport.active_runway))
    ux, uy = sin(rw_rad), cos(rw_rad)
    for ac in world.aircraft.values():
        if ac.role == "arrival" and ac.status in {"airborne", "on_final", "go_around"}:
            velx, vely = sin(radians(ac.heading_deg)), cos(radians(ac.heading_deg))
            inbound = (velx * (-ac.x_nm) + vely * (-ac.y_nm)) > 0
            along_final_nm = -(ac.x_nm * ux + ac.y_nm * uy)
            if inbound and along_final_nm > 0:
                dists.append(along_final_nm)
    return min(dists) if dists else None


def validate_actions(world: WorldState, actions: list[dict]) -> tuple[list[dict], list[dict]]:
    valid, invalid = [], []
    seen: set[str] = set()
    projected_time = world.time_sec
    projected_runway_release_sec = world.airport.runway_occupied_until_sec
    if world.airport.runway_occupied_by and projected_runway_release_sec is None:
        projected_runway_release_sec = projected_time + world.tick_sec
    for action in actions:
        cs = action.get("aircraft")
        atype = action.get("type")
        reason = None
        if cs not in world.aircraft:
            reason = "unknown_aircraft"
        elif atype not in ALLOWED_ACTION_TYPES:
            reason = "invalid_action_type"
        elif cs in seen and atype != "no_op":
            reason = "contradictory_commands"
        else:
            ac = world.aircraft[cs]
            if atype == "assign_heading" and not (0 <= action.get("heading", -1) <= 359):
                reason = "invalid_heading"
            elif atype == "assign_altitude" and action.get("altitude_ft", 0) < world.rules.min_altitude_ft:
                reason = "invalid_altitude"
            elif atype == "assign_speed" and not (world.rules.min_speed_kt <= action.get("speed_kt", -1) <= world.rules.max_speed_kt):
                reason = "invalid_speed"
            elif atype == "clear_to_land" and projected_runway_release_sec is not None and projected_runway_release_sec > projected_time:
                reason = "runway_occupied"
            elif atype == "clear_to_land" and ac.role != "arrival":
                reason = "not_arrival"
            elif atype == "clear_to_land" and ac.status not in {"airborne", "on_final"}:
                reason = "not_on_final_or_arrival"
            elif atype == "clear_for_takeoff" and projected_runway_release_sec is not None and projected_runway_release_sec > projected_time:
                reason = "runway_occupied"
            elif atype == "clear_for_takeoff" and cs not in world.airport.departure_queue:
                reason = "not_in_departure_queue"
            elif atype == "clear_for_takeoff":
                closest = _closest_inbound_arrival_final_distance(world)
                if closest is not None and closest < world.rules.runway_arrival_protection_nm:
                    reason = "arrival_too_close"
            elif atype == "go_around" and ac.status not in {"on_final", "airborne"}:
                reason = "not_on_approach"
        if reason:
            invalid.append({"action": action, "reason": reason})
        else:
            valid.append(action)
            seen.add(cs)
            if atype == "clear_to_land":
                projected_runway_release_sec = max(projected_runway_release_sec or projected_time, projected_time) + LANDING_RUNWAY_OCCUPANCY_SEC
            elif atype == "clear_for_takeoff":
                projected_runway_release_sec = max(projected_runway_release_sec or projected_time, projected_time) + TAKEOFF_RUNWAY_OCCUPANCY_SEC
    return valid, invalid
