from __future__ import annotations

from math import cos, radians, sin

from .models import ALLOWED_ACTION_TYPES, WorldState

TAKEOFF_RUNWAY_OCCUPANCY_SEC = 35
LANDING_RUNWAY_OCCUPANCY_SEC = 50


def _runway_heading_deg(active_runway: str) -> float:
    return (int(active_runway) % 36) * 10


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


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


def _is_aligned_for_active_runway(world: WorldState, aircraft) -> bool:
    if aircraft.target_runway is not None and aircraft.target_runway != world.airport.active_runway:
        return False
    for final_heading in (_runway_heading_deg(world.airport.active_runway), (_runway_heading_deg(world.airport.active_runway) + 180) % 360):
        heading_delta = _angle_delta_deg(aircraft.heading_deg, final_heading)
        rw_rad = radians(final_heading)
        ux, uy = sin(rw_rad), cos(rw_rad)
        along_final_nm = -(aircraft.x_nm * ux + aircraft.y_nm * uy)
        cross_track_nm = abs(aircraft.x_nm * uy - aircraft.y_nm * ux)
        if heading_delta <= 35 and 0 <= along_final_nm <= 15 and cross_track_nm <= 3:
            return True
    return False


def validate_actions(world: WorldState, actions: list[dict]) -> tuple[list[dict], list[dict]]:
    valid, invalid = [], []
    seen: set[str] = set()
    projected_time = world.time_sec
    projected_runway_release_sec = world.airport.runway_occupied_until_sec
    if world.airport.runway_occupied_by and projected_runway_release_sec is None:
        projected_runway_release_sec = projected_time + world.tick_sec
    layout = world.airport.layout if isinstance(world.airport.layout, dict) else {}
    known_waypoints = {
        fix.get("id")
        for key in ("fixes", "waypoints")
        for fix in layout.get(key, [])
        if isinstance(fix, dict) and isinstance(fix.get("id"), str)
    }
    for action in actions:
        cs = action.get("aircraft")
        atype = action.get("type")
        reason = None
        if atype == "no_op" and cs is None:
            valid.append(action)
            continue
        if not isinstance(cs, str) or cs not in world.aircraft:
            reason = "unknown_aircraft"
        elif atype not in ALLOWED_ACTION_TYPES:
            reason = "invalid_action_type"
        elif cs in seen and atype != "no_op":
            reason = "contradictory_commands"
        else:
            assert isinstance(cs, str)
            callsign = cs
            ac = world.aircraft[callsign]
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
            elif atype == "clear_to_land" and ac.status not in {"airborne", "on_final", "go_around"}:
                reason = "not_on_final_or_arrival"
            elif atype == "clear_to_land" and not _is_aligned_for_active_runway(world, ac):
                reason = "not_aligned_with_active_runway"
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
            elif atype == "hold_at_waypoint":
                if not isinstance(action.get("waypoint"), str) or not action.get("waypoint"):
                    reason = "invalid_waypoint"
                elif action.get("waypoint") not in known_waypoints:
                    reason = "unknown_waypoint"
                elif action.get("turn_direction") not in {"left", "right"}:
                    reason = "invalid_turn_direction"
                elif not isinstance(action.get("leg_length_nm"), (int, float)) or action.get("leg_length_nm") <= 0:
                    reason = "invalid_leg_length"
                elif not isinstance(action.get("hold_altitude_ft"), (int, float)) or action.get("hold_altitude_ft") < world.rules.min_altitude_ft:
                    reason = "invalid_hold_altitude"
            elif atype == "exit_hold" and ac.hold_fix_id is None:
                reason = "not_in_hold"
        if reason:
            invalid.append({"action": action, "reason": reason})
        else:
            valid.append(action)
            assert isinstance(cs, str)
            seen.add(cs)
            if atype == "clear_to_land":
                projected_runway_release_sec = max(projected_runway_release_sec or projected_time, projected_time) + LANDING_RUNWAY_OCCUPANCY_SEC
            elif atype == "clear_for_takeoff":
                projected_runway_release_sec = max(projected_runway_release_sec or projected_time, projected_time) + TAKEOFF_RUNWAY_OCCUPANCY_SEC
    return valid, invalid
