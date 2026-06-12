from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from atc_benchmark.agents.base import extract_actions
from atc_benchmark.paths import resolve_scenario_path

from .conflict_detection import detect_conflicts, predict_conflicts
from .decision_points import detect_decision_points
from .explain import (
    CallReason,
    CallReasonType,
    OutcomeKind,
    RankedAlternative,
    ScoreComponentId,
    TickExplanation,
    TickOutcome,
    tick_explanation_to_dict,
)
from .models import Aircraft, AirportState, RulesConfig, ScoringConfig, Weather, WorldState
from .scenario_validation import validate_scenario_document
from .validator import validate_actions

TAKEOFF_RUNWAY_OCCUPANCY_SEC = 35
LANDING_RUNWAY_OCCUPANCY_SEC = 50

AIRBORNE_DELAYED_ACTION_TYPES = {"assign_heading", "assign_altitude", "assign_speed", "clear_to_land", "go_around"}


def _runway_heading_deg(runway_id: str) -> float:
    n = int(runway_id)
    return 360.0 if n == 36 else float(n * 10)


def _angle_delta_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _runway_is_wind_compliant(world: WorldState, threshold_deg: float = 45.0) -> bool:
    heading = _runway_heading_deg(world.airport.active_runway)
    return _angle_delta_deg(world.weather.wind_dir_deg, heading) < threshold_deg


class ConflictLifecycleTracker:
    def __init__(self) -> None:
        self.state: dict[str, dict] = {}
        self.transition_counts = {
            "introduced": 0,
            "delayed": 0,
            "worsened": 0,
            "resolved": 0,
            "reintroduced": 0,
        }
        self.secondary_conflicts_created_count = 0

    def update(self, predictions: list[dict], *, is_action_phase: bool) -> None:
        current_pairs = {p["conflict_pair_id"]: p for p in predictions}
        active_pairs = {pair_id for pair_id, st in self.state.items() if st.get("active", False)}

        for pair_id, prediction in current_pairs.items():
            predicted_time = prediction["predicted_time_sec"]
            state = self.state.get(pair_id)
            if state is None:
                self.state[pair_id] = {
                    "first_predicted_time_sec": predicted_time,
                    "last_predicted_time_sec": predicted_time,
                    "active": True,
                    "events": ["introduced"],
                }
                self.transition_counts["introduced"] += 1
                continue

            if not state["active"]:
                state["active"] = True
                state["events"].append("reintroduced")
                self.transition_counts["reintroduced"] += 1
                self.secondary_conflicts_created_count += 1
            elif is_action_phase and predicted_time > state["last_predicted_time_sec"]:
                state["events"].append("delayed")
                self.transition_counts["delayed"] += 1
            elif is_action_phase and predicted_time < state["last_predicted_time_sec"]:
                state["events"].append("worsened")
                self.transition_counts["worsened"] += 1
            state["last_predicted_time_sec"] = predicted_time

        resolved_pairs = active_pairs - set(current_pairs.keys())
        for pair_id in resolved_pairs:
            state = self.state[pair_id]
            state["active"] = False
            state["events"].append("resolved")
            self.transition_counts["resolved"] += 1

    def average_time_gained_sec(self) -> float | None:
        if not self.state:
            return None
        total = sum(st["last_predicted_time_sec"] - st["first_predicted_time_sec"] for st in self.state.values())
        return total / len(self.state)


def _build_trigger_context(
    world: WorldState,
    decision_points: list[dict],
    triggered_events: list[dict],
) -> dict:
    snapshot = world.snapshot()
    event_ids = [event.get("id") or f"{event.get('type', 'event')}@{event.get('time_sec', world.time_sec)}" for event in triggered_events]
    dp_types = [dp.get("type", "unknown") for dp in decision_points]
    return {
        "provenance": {
            "source": "simulator.engine.run",
            "time_sec": world.time_sec,
            "tick_sec": world.tick_sec,
        },
        "decision_point_types": dp_types,
        "decision_point_count": len(decision_points),
        "triggered_event_ids": event_ids,
        "triggered_event_count": len(triggered_events),
        "thresholds": {
            "min_horizontal_nm": world.rules.min_horizontal_nm,
            "min_vertical_ft": world.rules.min_vertical_ft,
            "lookahead_seconds": world.rules.lookahead_seconds,
            "runway_arrival_protection_nm": world.rules.runway_arrival_protection_nm,
            "runway_wind_compliance_deg": 45.0,
        },
        "state_snapshot_keys": {
            "top_level": sorted(snapshot.keys()),
            "airport": sorted(snapshot.get("airport", {}).keys()),
            "weather": sorted(snapshot.get("weather", {}).keys()),
            "aircraft_callsigns": sorted(snapshot.get("aircraft", {}).keys()),
        },
    }


def _validate_trigger_context(trigger_context: dict) -> bool:
    provenance = trigger_context.get("provenance") if isinstance(trigger_context, dict) else None
    if not isinstance(provenance, dict):
        return False
    required = {"source", "time_sec", "tick_sec"}
    return required.issubset(provenance.keys())


def _named_fix_lookup(world: WorldState) -> dict[str, tuple[float, float]]:
    fixes: dict[str, tuple[float, float]] = {}
    layout = world.airport.layout if isinstance(world.airport.layout, dict) else {}
    for source in (layout.get("fixes", []), layout.get("waypoints", [])):
        if isinstance(source, list):
            for fix in source:
                if isinstance(fix, dict) and isinstance(fix.get("id"), str):
                    fixes[fix["id"]] = (float(fix["x_nm"]), float(fix["y_nm"]))
    return fixes


def _effective_turn_rate(world: WorldState, ac: Aircraft) -> float | None:
    if ac.max_turn_rate_deg_per_sec is not None:
        return ac.max_turn_rate_deg_per_sec
    return world.rules.default_turn_rate_deg_per_sec


def _turn_toward_target_heading(world: WorldState, ac: Aircraft) -> None:
    if ac.target_heading_deg is None or ac.hold_fix_id is not None:
        return
    rate = _effective_turn_rate(world, ac)
    if rate is None:
        ac.heading_deg = ac.target_heading_deg % 360
        ac.target_heading_deg = None
        return
    max_delta = rate * world.tick_sec
    delta = ((ac.target_heading_deg - ac.heading_deg + 180) % 360) - 180
    if abs(delta) <= max_delta:
        ac.heading_deg = ac.target_heading_deg % 360
        ac.target_heading_deg = None
    else:
        ac.heading_deg = (ac.heading_deg + math.copysign(max_delta, delta)) % 360


def _adjust_speed_toward_target(world: WorldState, ac: Aircraft) -> None:
    if ac.target_speed_kt is None:
        return
    rate = world.rules.speed_change_rate_kt_per_sec
    if rate is None:
        ac.speed_kt = ac.target_speed_kt
        ac.target_speed_kt = None
        return
    max_delta = rate * world.tick_sec
    delta = ac.target_speed_kt - ac.speed_kt
    if abs(delta) <= max_delta:
        ac.speed_kt = ac.target_speed_kt
        ac.target_speed_kt = None
    else:
        ac.speed_kt += math.copysign(max_delta, delta)


def advance(world: WorldState) -> list[dict]:
    events: list[dict] = []
    dt_hr = world.tick_sec / 3600
    for ac in world.aircraft.values():
        _update_managed_route(world, ac)
        if ac.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure", "holding"}:
            _turn_toward_target_heading(world, ac)
            _adjust_speed_toward_target(world, ac)
            rad = math.radians(ac.heading_deg)
            wind_to_deg = (world.weather.wind_dir_deg + 180) % 360
            wind_rad = math.radians(wind_to_deg)
            ground_vx_kt = math.sin(rad) * ac.speed_kt + math.sin(wind_rad) * world.weather.wind_speed_kt
            ground_vy_kt = math.cos(rad) * ac.speed_kt + math.cos(wind_rad) * world.weather.wind_speed_kt
            ac.x_nm += ground_vx_kt * dt_hr
            ac.y_nm += ground_vy_kt * dt_hr
            leg_nm = math.hypot(ground_vx_kt * dt_hr, ground_vy_kt * dt_hr)
            if ac.hold_fix_id and ac.hold_fix_x_nm is not None and ac.hold_fix_y_nm is not None:
                ac.status = "holding"
                if ac.hold_phase in {None, "outbound"}:
                    ac.hold_phase = "outbound"
                    ac.hold_leg_progress_nm += leg_nm
                    if ac.hold_leg_progress_nm >= (ac.hold_leg_length_nm or 0):
                        ac.hold_phase = "turn_to_inbound"
                        ac.hold_leg_progress_nm = 0.0
                        ac.hold_turn_remaining_deg = 180.0
                elif ac.hold_phase == "turn_to_inbound":
                    turn_delta = min(ac.hold_turn_remaining_deg, 3.0 * world.tick_sec)
                    sign = 1 if ac.hold_turn_direction == "right" else -1
                    ac.heading_deg = (ac.heading_deg + sign * turn_delta) % 360
                    ac.hold_turn_remaining_deg = max(0.0, ac.hold_turn_remaining_deg - turn_delta)
                    if ac.hold_turn_remaining_deg <= 0:
                        bearing = math.degrees(math.atan2(ac.hold_fix_x_nm - ac.x_nm, ac.hold_fix_y_nm - ac.y_nm)) % 360
                        ac.heading_deg = bearing
                        ac.hold_phase = "inbound"
                elif ac.hold_phase == "inbound":
                    bearing = math.degrees(math.atan2(ac.hold_fix_x_nm - ac.x_nm, ac.hold_fix_y_nm - ac.y_nm)) % 360
                    ac.heading_deg = bearing
                    if math.hypot(ac.hold_fix_x_nm - ac.x_nm, ac.hold_fix_y_nm - ac.y_nm) < 0.5:
                        ac.hold_phase = "outbound"
                        ac.hold_leg_progress_nm = 0.0
                        outbound = (bearing + 180) % 360
                        ac.heading_deg = outbound
            previous_altitude = ac.altitude_ft
            ac.altitude_ft += ac.vertical_rate_fpm * (world.tick_sec / 60)
            if ac.target_altitude_ft is not None:
                crossed_target = (
                    previous_altitude <= ac.target_altitude_ft <= ac.altitude_ft
                    or previous_altitude >= ac.target_altitude_ft >= ac.altitude_ft
                )
                if crossed_target:
                    ac.altitude_ft = ac.target_altitude_ft
                    ac.vertical_rate_fpm = 0
                    ac.target_altitude_ft = None
            if (
                ac.role == "arrival"
                and ac.clearance == "cleared_to_land"
                and abs(ac.x_nm) < 1.5
                and abs(ac.y_nm) < 1.5
                and ac.altitude_ft < 300
            ):
                occupant = world.airport.runway_occupied_by
                if occupant is not None and occupant != ac.callsign:
                    events.append({
                        "type": "runway_incursion",
                        "time_sec": world.time_sec + world.tick_sec,
                        "aircraft": ac.callsign,
                        "occupied_by": occupant,
                        "runway": world.airport.active_runway,
                    })
                ac.status = "landed"
                ac.landing_time_sec = world.time_sec + world.tick_sec
                world.airport.runway_occupied_by = ac.callsign
                world.airport.runway_phase = "vacating"
                world.airport.runway_occupied_until_sec = world.time_sec + world.tick_sec + LANDING_RUNWAY_OCCUPANCY_SEC
            if ac.status == "rolling":
                still_rolling = (
                    ac.takeoff_roll_until_sec is not None
                    and world.time_sec + world.tick_sec < ac.takeoff_roll_until_sec
                )
                if still_rolling:
                    ac.speed_kt = min(
                        world.rules.takeoff_rotation_speed_kt,
                        ac.speed_kt + world.rules.takeoff_acceleration_kt_per_sec * world.tick_sec,
                    )
                else:
                    if ac.takeoff_roll_until_sec is not None:
                        # Rotate: lift off at rotation speed and start the climb-out.
                        ac.takeoff_roll_until_sec = None
                        ac.speed_kt = max(ac.speed_kt, world.rules.takeoff_rotation_speed_kt)
                        ac.target_speed_kt = world.rules.climb_out_speed_kt
                        ac.vertical_rate_fpm = 2000
                        ac.target_altitude_ft = world.rules.climb_out_altitude_ft
                    ac.status = "airborne_departure"
                    ac.takeoff_time_sec = world.time_sec + world.tick_sec
        exit_nm = world.rules.airspace_exit_distance_nm
        if ac.status == "airborne_departure" and (abs(ac.x_nm) > exit_nm or abs(ac.y_nm) > exit_nm):
            ac.status = "exited_airspace"
    return events


def _update_managed_route(world: WorldState, ac: Aircraft) -> None:
    if not ac.waypoints or not ac.managed_route_active:
        return
    if ac.manual_override_until_sec is not None and world.time_sec < ac.manual_override_until_sec:
        return
    ac.manual_override_until_sec = None
    if ac.current_leg_index >= len(ac.waypoints):
        ac.current_leg_completed = True
        return
    wp = ac.waypoints[ac.current_leg_index]
    dx = wp["x_nm"] - ac.x_nm
    dy = wp["y_nm"] - ac.y_nm
    dist = math.hypot(dx, dy)
    if dist <= 0.5:
        ac.current_leg_completed = True
        ac.current_leg_index += 1
        return
    ac.current_leg_completed = False
    ac.heading_deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    min_alt = wp.get("min_altitude_ft")
    max_alt = wp.get("max_altitude_ft")
    if min_alt is not None and ac.altitude_ft < min_alt:
        ac.target_altitude_ft = min_alt
        ac.vertical_rate_fpm = 1000
    elif max_alt is not None and ac.altitude_ft > max_alt:
        ac.target_altitude_ft = max_alt
        ac.vertical_rate_fpm = -1000
    if wp.get("speed_kt") is not None:
        ac.speed_kt = wp["speed_kt"]


def _point_in_polygon(x_nm: float, y_nm: float, vertices: list[dict]) -> bool:
    inside = False
    j = len(vertices) - 1
    for i in range(len(vertices)):
        xi = vertices[i]["x_nm"]
        yi = vertices[i]["y_nm"]
        xj = vertices[j]["x_nm"]
        yj = vertices[j]["y_nm"]
        intersects = ((yi > y_nm) != (yj > y_nm)) and (x_nm < (xj - xi) * (y_nm - yi) / ((yj - yi) or 1e-12) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def _detect_restricted_zone_crossings(world: WorldState, prior_positions: dict[str, tuple[float, float]], violated: set[tuple[str, str]]) -> list[dict]:
    events: list[dict] = []
    zones = world.rules.restricted_zones or []
    for callsign, ac in world.aircraft.items():
        previous = prior_positions.get(callsign)
        if previous is None:
            continue
        for zone in zones:
            zone_id = zone["id"]
            key = (callsign, zone_id)
            prev_inside = _point_in_polygon(previous[0], previous[1], zone["vertices"])
            now_inside = _point_in_polygon(ac.x_nm, ac.y_nm, zone["vertices"])
            if not prev_inside and now_inside and key not in violated:
                violated.add(key)
                events.append({
                    "type": "restricted_zone_violation",
                    "time_sec": world.time_sec + world.tick_sec,
                    "aircraft": callsign,
                    "zone_id": zone_id,
                    "position_nm": {"x_nm": ac.x_nm, "y_nm": ac.y_nm},
                })
    return events

def apply_actions(world: WorldState, actions: list[dict]) -> dict:
    go_arounds = 0
    fix_lookup = _named_fix_lookup(world)
    for action in actions:
        t = action["type"]
        if t == "no_op":
            continue
        ac = world.aircraft[action["aircraft"]]
        if t == "assign_heading":
            desired_heading = action["heading"] % 360
            if _effective_turn_rate(world, ac) is None:
                ac.heading_deg = desired_heading
                ac.target_heading_deg = None
            else:
                # Turn-rate-limited aircraft turn progressively each tick in advance().
                ac.target_heading_deg = desired_heading
            ac.managed_route_active = False
            ac.manual_override_until_sec = world.time_sec + 120
        elif t == "assign_altitude":
            target = action["altitude_ft"]
            ac.target_altitude_ft = target
            if target == ac.altitude_ft:
                ac.vertical_rate_fpm = 0
            elif target > ac.altitude_ft:
                ac.vertical_rate_fpm = ac.max_climb_fpm if ac.max_climb_fpm is not None else 1500
            else:
                descent_limit = ac.max_descent_fpm if ac.max_descent_fpm is not None else 1500
                ac.vertical_rate_fpm = -descent_limit
            ac.managed_route_active = False
            ac.manual_override_until_sec = world.time_sec + 120
        elif t == "assign_speed":
            speed = action["speed_kt"]
            if ac.max_speed_kt is not None:
                speed = min(speed, ac.max_speed_kt)
            if ac.min_speed_kt is not None:
                speed = max(speed, ac.min_speed_kt)
            if world.rules.speed_change_rate_kt_per_sec is None:
                ac.speed_kt = speed
                ac.target_speed_kt = None
            else:
                # Speed changes ramp progressively each tick in advance().
                ac.target_speed_kt = speed
            ac.managed_route_active = False
            ac.manual_override_until_sec = world.time_sec + 120
        elif t == "clear_to_land":
            ac.clearance = "cleared_to_land"
            ac.status = "on_final"
        elif t == "clear_for_takeoff":
            ac.clearance = "cleared_for_takeoff"
            ac.status = "rolling"
            if ac.ready_time_sec is None:
                ac.ready_time_sec = world.time_sec
            roll_sec = world.rules.takeoff_roll_sec
            if roll_sec > 0:
                ac.takeoff_roll_until_sec = world.time_sec + roll_sec
                ac.heading_deg = _runway_heading_deg(world.airport.active_runway) % 360
                ac.target_heading_deg = None
            world.airport.runway_occupied_by = ac.callsign
            world.airport.runway_phase = "takeoff_roll"
            world.airport.runway_occupied_until_sec = world.time_sec + max(TAKEOFF_RUNWAY_OCCUPANCY_SEC, roll_sec)
            if ac.callsign in world.airport.departure_queue:
                world.airport.departure_queue.remove(ac.callsign)
        elif t == "go_around":
            ac.status = "go_around"
            ac.clearance = None
            ac.vertical_rate_fpm = 1200
            ac.target_altitude_ft = max(3000.0, ac.altitude_ft)
            go_arounds += 1
        elif t in {"hold_short", "hold_position"}:
            ac.speed_kt = 0
            ac.managed_route_active = False
            ac.manual_override_until_sec = world.time_sec + 120
        elif t == "hold_at_waypoint":
            waypoint = action["waypoint"]
            fx, fy = fix_lookup[waypoint]
            ac.hold_fix_id = waypoint
            ac.hold_fix_x_nm = fx
            ac.hold_fix_y_nm = fy
            ac.hold_leg_length_nm = float(action["leg_length_nm"])
            ac.hold_turn_direction = action["turn_direction"]
            ac.hold_altitude_ft = float(action["hold_altitude_ft"])
            ac.target_altitude_ft = ac.hold_altitude_ft
            ac.vertical_rate_fpm = 1500 if ac.hold_altitude_ft > ac.altitude_ft else -1500 if ac.hold_altitude_ft < ac.altitude_ft else 0
            ac.hold_phase = "inbound"
            ac.hold_leg_progress_nm = 0.0
            ac.hold_turn_remaining_deg = 0.0
            ac.target_heading_deg = None
            ac.managed_route_active = False
            ac.manual_override_until_sec = None
            ac.status = "holding"
        elif t == "exit_hold":
            ac.hold_fix_id = None
            ac.hold_fix_x_nm = None
            ac.hold_fix_y_nm = None
            ac.hold_leg_length_nm = None
            ac.hold_turn_direction = None
            ac.hold_altitude_ft = None
            ac.hold_phase = None
            ac.hold_leg_progress_nm = 0.0
            ac.hold_turn_remaining_deg = 0.0
            if ac.status == "holding":
                ac.status = "airborne"
            ac.managed_route_active = False
            ac.manual_override_until_sec = world.time_sec + 120
        elif t == "resume_procedure":
            ac.managed_route_active = True
            ac.manual_override_until_sec = None
            ac.target_heading_deg = None
            ac.target_speed_kt = None
    return {"go_around_count": go_arounds}




def _sample_command_delay_sec(world: WorldState, rng: random.Random) -> int:
    delay = world.rules.pilot_readback_delay_sec or {"min": 0, "max": 0}
    min_delay = int(delay.get("min", 0))
    max_delay = int(delay.get("max", min_delay))
    if max_delay < min_delay:
        min_delay, max_delay = max_delay, min_delay
    return rng.randint(min_delay, max_delay)


def _enqueue_actions(world: WorldState, pending_commands: list[dict], valid_actions: list[dict], rng: random.Random) -> list[dict]:
    enqueued: list[dict] = []
    for action in valid_actions:
        action_type = action["type"]
        callsign = action.get("aircraft")
        ac = world.aircraft.get(callsign) if isinstance(callsign, str) else None
        should_delay = (
            ac is not None
            and action_type in AIRBORNE_DELAYED_ACTION_TYPES
            and ac.status in {"airborne", "on_final", "go_around", "airborne_departure"}
        )
        delay_sec = _sample_command_delay_sec(world, rng) if should_delay else 0
        entry = {
            "action": action,
            "issued_at_sec": world.time_sec,
            "scheduled_execution_time_sec": world.time_sec + delay_sec,
        }
        pending_commands.append(entry)
        enqueued.append(entry)
    return enqueued


def _drain_due_actions(world: WorldState, pending_commands: list[dict]) -> list[dict]:
    ready: list[dict] = []
    future: list[dict] = []
    for command in pending_commands:
        if world.time_sec >= command["scheduled_execution_time_sec"]:
            ready.append(command["action"])
        else:
            future.append(command)
    pending_commands[:] = future
    return ready
def apply_events(world: WorldState) -> list[dict]:
    triggered: list[dict] = []
    for event in world.events:
        if event.get("applied"):
            continue
        time_sec = event.get("time_sec")
        # Fire at the first tick at-or-after the scheduled time so off-tick
        # event times are not silently skipped.
        if not isinstance(time_sec, int) or time_sec > world.time_sec:
            continue
        event["applied"] = True
        etype = event["type"]
        if etype == "wind_change":
            world.weather.wind_dir_deg = event["wind_dir_deg"]
            world.weather.wind_speed_kt = event["wind_speed_kt"]
            if "active_runway" in event:
                world.airport.active_runway = event["active_runway"]
        elif etype == "emergency_declare":
            world.aircraft[event["aircraft"]].emergency = True
        elif etype == "low_fuel_emergency":
            ac = world.aircraft[event["aircraft"]]
            ac.emergency = True
            ac.emergency_subtype = "low_fuel"
            ac.emergency_deadline_sec = event.get("deadline_sec")
            ac.emergency_remaining_endurance_sec = event.get("remaining_endurance_sec")
            ac.emergency_require_return_to_land = True
        elif etype == "engine_failure":
            ac = world.aircraft[event["aircraft"]]
            ac.emergency = True
            ac.emergency_subtype = "engine_failure"
            ac.emergency_require_return_to_land = event.get("require_return_to_land", True)
            ac.max_climb_fpm = event.get("max_climb_fpm", 500)
            ac.max_descent_fpm = event.get("max_descent_fpm", 1200)
            ac.max_speed_kt = event.get("max_speed_kt", 210)
            ac.min_speed_kt = event.get("min_speed_kt", 130)
            ac.max_turn_rate_deg_per_sec = event.get("max_turn_rate_deg_per_sec", 2.0)
        triggered.append({k: v for k, v in event.items() if k != "applied"})
    return triggered


def _update_emergency_state(world: WorldState) -> int:
    terminal_failures = 0
    for ac in world.aircraft.values():
        if ac.emergency_subtype == "low_fuel" and ac.status not in {"landed", "exited_airspace"}:
            if ac.emergency_remaining_endurance_sec is not None:
                ac.emergency_remaining_endurance_sec = max(0, ac.emergency_remaining_endurance_sec - world.tick_sec)
                if ac.emergency_remaining_endurance_sec == 0:
                    ac.emergency_terminal_failure = True
            if ac.emergency_deadline_sec is not None and world.time_sec >= ac.emergency_deadline_sec:
                ac.emergency_terminal_failure = True
        if ac.emergency_terminal_failure and ac.status not in {"terminal_failure", "landed"}:
            ac.status = "terminal_failure"
            terminal_failures += 1
    return terminal_failures


def _build_score_result(
    world: WorldState,
    lifecycle: ConflictLifecycleTracker,
    *,
    loss_sep_count: int,
    min_h: float,
    min_v: float,
    invalid_count: int,
    malformed_agent_outputs_count: int,
    instructions: int,
    conflict_predicted_times: list[int],
    go_around_count: int,
    wind_response_latency_sec: int | None,
    unsafe_clearances_after_wind_change: int,
    emergency_priority_compliant_count: int,
    emergency_priority_violation_count: int,
    active_conflicts_count_total: int,
    predicted_conflicts_count_total: int,
    manifest: dict | None,
    restricted_zone_violation_count: int,
    runway_incursion_count: int = 0,
    emergency_handled_by_type: dict[str, int] | None = None,
    emergency_unhandled_by_type: dict[str, int] | None = None,
) -> dict:
    conflict_introduced_count = lifecycle.transition_counts["introduced"]
    conflict_resolved_count = lifecycle.transition_counts["resolved"]
    conflict_reintroduced_count = lifecycle.transition_counts["reintroduced"]
    conflicts_delayed_count = lifecycle.transition_counts["delayed"]
    conflicts_worsened_count = lifecycle.transition_counts["worsened"]
    secondary_conflicts_created_count = lifecycle.secondary_conflicts_created_count

    arrivals = [a for a in world.aircraft.values() if a.role == "arrival"]
    departures = [a for a in world.aircraft.values() if a.role == "departure"]
    landings = sum(1 for a in arrivals if a.status == "landed")
    departures_ok = sum(1 for a in departures if a.status in {"airborne_departure", "exited_airspace", "landed"})
    arrival_delay = sum(max(0, (a.landing_time_sec or world.time_sec) - a.ideal_landing_time_sec) for a in arrivals if a.ideal_landing_time_sec is not None)
    departure_delay = sum(max(0, (a.takeoff_time_sec or world.time_sec) - a.ideal_takeoff_time_sec) for a in departures if a.ideal_takeoff_time_sec is not None)
    emergency_handled_count = sum(1 for a in arrivals if a.emergency and a.status == "landed")
    emergency_unhandled_count = sum(1 for a in arrivals if a.emergency and a.status != "landed")
    scoring = world.scoring
    score_breakdown = {
        ScoreComponentId.BASE_SCORE: scoring.base_score,
        ScoreComponentId.LOSS_OF_SEPARATION: loss_sep_count * scoring.loss_of_separation_penalty,
        ScoreComponentId.INVALID_COMMAND: invalid_count * scoring.invalid_command_penalty,
        ScoreComponentId.SECONDARY_CONFLICTS_CREATED: secondary_conflicts_created_count * scoring.secondary_conflicts_created_penalty,
        ScoreComponentId.CONFLICTS_WORSENED: conflicts_worsened_count * scoring.conflicts_worsened_penalty,
        ScoreComponentId.CONFLICTS_DELAYED: conflicts_delayed_count * scoring.conflicts_delayed_reward,
        ScoreComponentId.CONFLICT_RESOLVED: conflict_resolved_count * scoring.conflict_resolved_reward,
        ScoreComponentId.ARRIVAL_DELAY_SEC: arrival_delay * scoring.arrival_delay_sec_penalty,
        ScoreComponentId.DEPARTURE_DELAY_SEC: departure_delay * scoring.departure_delay_sec_penalty,
        ScoreComponentId.SUCCESSFUL_LANDING: landings * scoring.successful_landing_reward,
        ScoreComponentId.SUCCESSFUL_DEPARTURE: departures_ok * scoring.successful_departure_reward,
        ScoreComponentId.EMERGENCY_HANDLED: emergency_handled_count * scoring.emergency_handled_reward,
        ScoreComponentId.EMERGENCY_UNHANDLED: emergency_unhandled_count * scoring.emergency_unhandled_penalty,
        ScoreComponentId.EMERGENCY_PRIORITY_COMPLIANCE: (
            emergency_priority_compliant_count * scoring.emergency_priority_compliance_reward
            + emergency_priority_violation_count * scoring.emergency_priority_violation_penalty
        ),
        ScoreComponentId.RESTRICTED_ZONE_VIOLATION: restricted_zone_violation_count * scoring.restricted_zone_violation_penalty,
        ScoreComponentId.RUNWAY_INCURSION: runway_incursion_count * scoring.runway_incursion_penalty,
    }
    raw_score = sum(score_breakdown.values())
    simulated_hours = world.time_sec / 3600 if world.time_sec > 0 else 0.0
    throughput_ops_per_hour = ((landings + departures_ok) / simulated_hours) if simulated_hours > 0 else 0.0
    result = {
        "score": max(0.0, raw_score),
        "score_breakdown": score_breakdown,
        "safety": {
            "loss_of_separation": loss_sep_count,
            "min_horizontal_nm": min_h if min_h < float("inf") else None,
            "min_vertical_ft": min_v if min_v < float("inf") else None,
        },
        "efficiency": {"successful_landings": landings, "successful_departures": departures_ok, "arrival_delay": arrival_delay, "departure_delay": departure_delay},
        "control_quality": {"instructions_issued": instructions, "invalid_commands": invalid_count},
        "metrics": {
            "conflict_predicted_time": min(conflict_predicted_times) if conflict_predicted_times else None,
            "conflict_resolved_count": conflict_resolved_count,
            "conflict_introduced_count": conflict_introduced_count,
            "conflict_reintroduced_count": conflict_reintroduced_count,
            "conflicts_delayed_count": conflicts_delayed_count,
            "conflicts_worsened_count": conflicts_worsened_count,
            "average_conflict_time_gained_sec": lifecycle.average_time_gained_sec(),
            "secondary_conflicts_created_count": secondary_conflicts_created_count,
            "go_around_count": go_around_count,
            "emergency_handled_count": emergency_handled_count,
            "emergency_unhandled_count": emergency_unhandled_count,
            "wind_response_latency_sec": wind_response_latency_sec,
            "wind_change_response_latency_sec": wind_response_latency_sec,
            "unsafe_clearances_after_wind_change": unsafe_clearances_after_wind_change,
            "runway_unsafe_clearance_count": unsafe_clearances_after_wind_change,
            "emergency_priority_compliant_count": emergency_priority_compliant_count,
            "emergency_priority_violation_count": emergency_priority_violation_count,
            "malformed_agent_outputs_count": malformed_agent_outputs_count,
            "active_conflicts_count_total": active_conflicts_count_total,
            "predicted_conflicts_count_total": predicted_conflicts_count_total,
            "throughput_ops_per_hour": throughput_ops_per_hour,
            "restricted_zone_violation_count": restricted_zone_violation_count,
            "runway_incursion_count": runway_incursion_count,
            "emergency_handled_by_type": emergency_handled_by_type or {},
            "emergency_unhandled_by_type": emergency_unhandled_by_type or {},
        },
    }
    if manifest is not None:
        result["run_manifest"] = manifest
    return result


def _build_tick_explanation(
    *,
    tick_id: int,
    world: WorldState,
    dps: list[dict],
    triggered_events: list[dict],
    trigger_context: dict,
    actions: list[dict],
    conflicts: list[dict],
    invalid: list[dict],
    cumulative_score_components: dict[str, float],
) -> TickExplanation:
    call_reason_type = CallReasonType.NONE
    if any(dp.get("type") == "event" for dp in dps):
        call_reason_type = CallReasonType.EVENT
    elif dps:
        call_reason_type = CallReasonType.DECISION_POINT

    score_before = sum(cumulative_score_components.values())
    delta_by_component = {
        ScoreComponentId.LOSS_OF_SEPARATION: len(conflicts) * world.scoring.loss_of_separation_penalty if conflicts else 0.0,
        ScoreComponentId.INVALID_COMMAND: len(invalid) * world.scoring.invalid_command_penalty if invalid else 0.0,
    }
    for component, delta in delta_by_component.items():
        cumulative_score_components[component] += delta

    score_after = sum(cumulative_score_components.values())
    score_diff = score_after - score_before
    normalization_value = max(abs(world.scoring.base_score), world.rules.outcome_normalization_floor)
    normalized_immediate_delta = score_diff / normalization_value
    return TickExplanation(
        tick_id=tick_id,
        sim_time=world.time_sec,
        call_reason=CallReason(
            type=call_reason_type.value,
            details={
                "decision_point_count": len(dps),
                "triggered_event_count": len(triggered_events),
            },
        ),
        trigger_context=trigger_context,
        action_chosen=actions or [],
        alternatives_considered=[RankedAlternative(rank=1, action={"type": "no_op"}, score=None)],
        outcome=TickOutcome(
            kind=OutcomeKind.UNKNOWN.value,
            metric="normalized_tick_score_delta",
            value=normalized_immediate_delta,
            immediate_delta=normalized_immediate_delta,
            normalization_value=normalization_value,
            epsilon_immediate=world.rules.outcome_immediate_epsilon,
            epsilon_window=world.rules.outcome_window_epsilon,
            horizon_ticks=world.rules.outcome_horizon_ticks,
        ),
        score_before=score_before,
        score_after=score_after,
        score_delta_by_component=delta_by_component,
    )


def _finalize_tick_outcomes(world: WorldState, tick_records: list[dict]) -> None:
    for idx, tick_record in enumerate(tick_records):
        explanation = tick_record["tick_explanation_obj"]
        horizon_idx = min(idx + world.rules.outcome_horizon_ticks, len(tick_records) - 1)
        window_delta = tick_records[horizon_idx]["tick_explanation_obj"].score_after - explanation.score_before
        normalization_value = explanation.outcome.normalization_value or 1.0
        normalized_window_delta = window_delta / normalization_value
        explanation.outcome.window_delta = normalized_window_delta

        immediate = explanation.outcome.immediate_delta or 0.0
        if abs(immediate) <= world.rules.outcome_immediate_epsilon and abs(normalized_window_delta) <= world.rules.outcome_window_epsilon:
            explanation.outcome.kind = OutcomeKind.NEUTRAL.value
        elif normalized_window_delta > world.rules.outcome_window_epsilon:
            explanation.outcome.kind = OutcomeKind.HELPED.value
        elif normalized_window_delta < -world.rules.outcome_window_epsilon:
            explanation.outcome.kind = OutcomeKind.HURT.value
        elif immediate > world.rules.outcome_immediate_epsilon:
            explanation.outcome.kind = OutcomeKind.HELPED.value
        elif immediate < -world.rules.outcome_immediate_epsilon:
            explanation.outcome.kind = OutcomeKind.HURT.value
        else:
            explanation.outcome.kind = OutcomeKind.NEUTRAL.value


class SimulationStepper:
    """Drives the simulation one tick at a time and owns all run metrics.

    Shared by the batch runner (`run`) and the live server so both modes use
    identical semantics: pilot readback delays, emergency state progression,
    restricted-zone and runway-incursion tracking, and scoring.

    Tick protocol: `begin_tick()` applies events and detects the situation,
    `submit_actions()` validates and enqueues commands (any number of times
    between begin and finish; live commands submitted between ticks are
    recorded in the next tick), `finish_tick()` executes due commands,
    records the tick, and advances the world.
    """

    TERMINAL_STATUSES = {"landed", "exited_airspace", "terminal_failure"}

    def __init__(self, world: WorldState) -> None:
        self.world = world
        self.invalid_count = 0
        self.malformed_agent_outputs_count = 0
        self.instructions = 0
        self.loss_sep_count = 0
        self.min_h = float("inf")
        self.min_v = float("inf")
        self.conflict_predicted_times: list[int] = []
        self.go_around_count = 0
        self.latest_wind_change_sec: int | None = None
        self.wind_response_latency_sec: int | None = None
        self.unsafe_clearances_after_wind_change = 0
        self.emergency_priority_compliant_count = 0
        self.emergency_priority_violation_count = 0
        self.active_conflicts_count_total = 0
        self.predicted_conflicts_count_total = 0
        self.restricted_zone_violation_count = 0
        self.runway_incursion_count = 0
        self.violated_zone_entries: set[tuple[str, str]] = set()
        self.lifecycle = ConflictLifecycleTracker()
        self.cumulative_score_components: dict[str, float] = {
            ScoreComponentId.BASE_SCORE: world.scoring.base_score,
            ScoreComponentId.LOSS_OF_SEPARATION: 0.0,
            ScoreComponentId.INVALID_COMMAND: 0.0,
            ScoreComponentId.SECONDARY_CONFLICTS_CREATED: 0.0,
            ScoreComponentId.CONFLICTS_WORSENED: 0.0,
            ScoreComponentId.CONFLICTS_DELAYED: 0.0,
            ScoreComponentId.CONFLICT_RESOLVED: 0.0,
            ScoreComponentId.ARRIVAL_DELAY_SEC: 0.0,
            ScoreComponentId.DEPARTURE_DELAY_SEC: 0.0,
            ScoreComponentId.SUCCESSFUL_LANDING: 0.0,
            ScoreComponentId.SUCCESSFUL_DEPARTURE: 0.0,
            ScoreComponentId.EMERGENCY_HANDLED: 0.0,
            ScoreComponentId.EMERGENCY_UNHANDLED: 0.0,
            ScoreComponentId.EMERGENCY_PRIORITY_COMPLIANCE: 0.0,
            ScoreComponentId.RESTRICTED_ZONE_VIOLATION: 0.0,
            ScoreComponentId.RUNWAY_INCURSION: 0.0,
        }
        self.tick_records: list[dict] = []
        self.pending_commands: list[dict] = []
        self.rng = random.Random(world.rules.command_delay_seed)
        self.tick_id = 0
        self._submitted_actions: list[dict] = []
        self._submitted_invalid: list[dict] = []
        self._observation: dict | None = None
        self._agent_exception: dict | None = None
        self._current: dict | None = None

    def begin_tick(self) -> dict:
        world = self.world
        triggered_events = apply_events(world)
        for event in triggered_events:
            if event.get("type") == "wind_change":
                self.latest_wind_change_sec = world.time_sec
                self.wind_response_latency_sec = None
        conflicts = detect_conflicts(world)
        if conflicts:
            self.loss_sep_count += len(conflicts)
            self.min_h = min(self.min_h, min(c["horizontal_nm"] for c in conflicts))
            self.min_v = min(self.min_v, min(c["vertical_ft"] for c in conflicts))
        self.active_conflicts_count_total += len(conflicts)

        predictions = predict_conflicts(world)
        self.lifecycle.update(predictions, is_action_phase=False)
        self.conflict_predicted_times.extend(p["in_seconds"] for p in predictions)
        self.predicted_conflicts_count_total += len(predictions)
        dps = detect_decision_points(world, conflicts=conflicts, predictions=predictions)
        for event in triggered_events:
            dps.append({"type": "event", "event": event})

        trigger_context = _build_trigger_context(world, dps, triggered_events)
        if world.rules.debug_require_trigger_provenance and not _validate_trigger_context(trigger_context):
            raise ValueError("Missing trigger provenance for invocation in debug mode")
        self._current = {
            "triggered_events": triggered_events,
            "decision_points": dps,
            "conflicts": conflicts,
            "predictions": predictions,
            "trigger_context": trigger_context,
        }
        return self._current

    def submit_actions(
        self,
        raw_actions: list[dict],
        *,
        malformed: list[dict] | None = None,
        observation: dict | None = None,
        agent_exception: dict | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Validate and enqueue actions. Returns (enqueued_entries, invalid_records)."""
        world = self.world
        malformed = malformed or []
        if observation is not None:
            self._observation = observation
        if agent_exception is not None:
            self._agent_exception = agent_exception
        self.instructions += len(raw_actions)
        emergency_callsigns = {
            ac.callsign for ac in world.aircraft.values() if ac.role == "arrival" and ac.emergency and ac.status != "landed"
        }
        if emergency_callsigns:
            for action in raw_actions:
                if not isinstance(action, dict):
                    continue
                if action.get("type") == "clear_for_takeoff":
                    self.emergency_priority_violation_count += 1
                elif action.get("type") == "clear_to_land":
                    if action.get("aircraft") in emergency_callsigns:
                        self.emergency_priority_compliant_count += 1
                    else:
                        self.emergency_priority_violation_count += 1
        valid, invalid = validate_actions(world, raw_actions)
        invalid = malformed + invalid
        self.malformed_agent_outputs_count += len(malformed)
        self.invalid_count += len(invalid)
        enqueued = _enqueue_actions(world, self.pending_commands, valid, self.rng)
        self._submitted_actions.extend(raw_actions)
        self._submitted_invalid.extend(invalid)
        return enqueued, invalid

    def submit_command(self, command: dict) -> tuple[dict | None, dict | None]:
        """Validate and enqueue one live command. Returns (enqueued_entry, invalid_record)."""
        enqueued, invalid = self.submit_actions([command])
        if invalid:
            return None, invalid[0]
        return (enqueued[0] if enqueued else None), None

    def finish_tick(self) -> dict:
        world = self.world
        cur = self._current
        if cur is None:
            cur = self.begin_tick()
        actions = self._submitted_actions
        invalid = self._submitted_invalid
        observation = self._observation
        agent_exception = self._agent_exception
        self._submitted_actions = []
        self._submitted_invalid = []
        self._observation = None
        self._agent_exception = None
        self._current = None

        due_actions = _drain_due_actions(world, self.pending_commands)
        if due_actions:
            effects = apply_actions(world, due_actions)
            if self.latest_wind_change_sec is not None and not _runway_is_wind_compliant(world):
                self.unsafe_clearances_after_wind_change += sum(
                    1 for a in due_actions if a["type"] in {"clear_to_land", "clear_for_takeoff"}
                )
            self.go_around_count += effects["go_around_count"]
            after_predictions = predict_conflicts(world)
            self.lifecycle.update(after_predictions, is_action_phase=True)

        if self.latest_wind_change_sec is not None and self.wind_response_latency_sec is None and _runway_is_wind_compliant(world):
            self.wind_response_latency_sec = world.time_sec - self.latest_wind_change_sec

        explanation = _build_tick_explanation(
            tick_id=self.tick_id,
            world=world,
            dps=cur["decision_points"],
            triggered_events=cur["triggered_events"],
            trigger_context=cur["trigger_context"],
            actions=actions,
            conflicts=cur["conflicts"],
            invalid=invalid,
            cumulative_score_components=self.cumulative_score_components,
        )
        record = {
            "time": world.time_sec,
            "triggered_events": cur["triggered_events"],
            "decision_points": cur["decision_points"],
            "observation": observation,
            "agent_exception": agent_exception,
            "actions": actions,
            "invalid_actions": invalid,
            "conflicts": cur["conflicts"],
            "predicted_conflicts": cur["predictions"],
            "state": world.snapshot(),
            "tick_explanation_obj": explanation,
        }
        self.tick_records.append(record)

        prior_positions = {k: (a.x_nm, a.y_nm) for k, a in world.aircraft.items()}
        advance_events = advance(world)
        self.runway_incursion_count += sum(1 for e in advance_events if e.get("type") == "runway_incursion")
        restricted_events = _detect_restricted_zone_crossings(world, prior_positions, self.violated_zone_entries)
        self.restricted_zone_violation_count += len(restricted_events)
        record["triggered_events"].extend(advance_events)
        record["triggered_events"].extend(restricted_events)
        world.time_sec += world.tick_sec
        if world.airport.runway_occupied_until_sec is not None and world.time_sec >= world.airport.runway_occupied_until_sec:
            world.airport.runway_occupied_by = None
            world.airport.runway_phase = None
            world.airport.runway_occupied_until_sec = None
        _update_emergency_state(world)
        self.tick_id += 1
        return record

    def is_complete(self) -> bool:
        return bool(self.world.aircraft) and all(
            a.status in self.TERMINAL_STATUSES for a in self.world.aircraft.values()
        )

    def finalize(self) -> None:
        _finalize_tick_outcomes(self.world, self.tick_records)

    @staticmethod
    def record_to_event(record: dict) -> dict:
        payload = {k: v for k, v in record.items() if k != "tick_explanation_obj"}
        payload["tick_explanation"] = tick_explanation_to_dict(record["tick_explanation_obj"])
        return payload

    def write_trace(self, trace_path: Path) -> None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("w", encoding="utf-8") as f:
            for tick_record in self.tick_records:
                f.write(json.dumps(self.record_to_event(tick_record)) + "\n")

    def build_score(self, manifest: dict | None = None) -> dict:
        emergency_handled_by_type = {"low_fuel": 0, "engine_failure": 0, "generic": 0}
        emergency_unhandled_by_type = {"low_fuel": 0, "engine_failure": 0, "generic": 0}
        for ac in self.world.aircraft.values():
            if not ac.emergency:
                continue
            key = ac.emergency_subtype or "generic"
            if ac.status == "landed":
                emergency_handled_by_type[key] = emergency_handled_by_type.get(key, 0) + 1
            else:
                emergency_unhandled_by_type[key] = emergency_unhandled_by_type.get(key, 0) + 1
        return _build_score_result(
            self.world,
            self.lifecycle,
            loss_sep_count=self.loss_sep_count,
            min_h=self.min_h,
            min_v=self.min_v,
            invalid_count=self.invalid_count,
            malformed_agent_outputs_count=self.malformed_agent_outputs_count,
            instructions=self.instructions,
            conflict_predicted_times=self.conflict_predicted_times,
            go_around_count=self.go_around_count,
            wind_response_latency_sec=self.wind_response_latency_sec,
            unsafe_clearances_after_wind_change=self.unsafe_clearances_after_wind_change,
            emergency_priority_compliant_count=self.emergency_priority_compliant_count,
            emergency_priority_violation_count=self.emergency_priority_violation_count,
            active_conflicts_count_total=self.active_conflicts_count_total,
            predicted_conflicts_count_total=self.predicted_conflicts_count_total,
            manifest=manifest,
            restricted_zone_violation_count=self.restricted_zone_violation_count,
            runway_incursion_count=self.runway_incursion_count,
            emergency_handled_by_type=emergency_handled_by_type,
            emergency_unhandled_by_type=emergency_unhandled_by_type,
        )


def run(world: WorldState, agent, max_ticks: int, trace_path: Path, manifest: dict | None = None) -> dict:
    stepper = SimulationStepper(world)
    for _ in range(max_ticks):
        ctx = stepper.begin_tick()
        dps = ctx["decision_points"]
        if dps:
            obs = {
                "time_sec": world.time_sec,
                "decision_points": dps,
                "snapshot": world.snapshot(),
                "trigger_context": ctx["trigger_context"],
            }
            try:
                agent_output = agent.act(obs)
            except Exception as exc:  # noqa: BLE001
                stepper.submit_actions(
                    [],
                    malformed=[{
                        "action": None,
                        "reason": "agent_exception",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    }],
                    observation=obs,
                    agent_exception={"type": type(exc).__name__, "message": str(exc)},
                )
            else:
                raw_actions, malformed = extract_actions(agent_output)
                stepper.submit_actions(raw_actions, malformed=malformed, observation=obs)
        stepper.finish_tick()
        if stepper.is_complete():
            break

    stepper.finalize()
    stepper.write_trace(trace_path)
    return stepper.build_score(manifest)


def scenario_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_world(path: Path) -> WorldState:
    path = resolve_scenario_path(path)
    data = json.loads(path.read_text())
    validate_scenario_document(data, path)
    ac = {a["callsign"]: Aircraft(**a) for a in data["aircraft"]}
    for aircraft in ac.values():
        if aircraft.role == "departure" and aircraft.status == "departed":
            aircraft.status = "airborne_departure"
        if aircraft.role == "departure" and aircraft.status == "waiting_departure" and aircraft.ready_time_sec is None:
            aircraft.ready_time_sec = 0
    scoring_data = data.get("scoring", {})
    airport_data = dict(data["airport"])
    if data.get("waypoints"):
        layout = dict(airport_data.get("layout") or {})
        layout["waypoints"] = data["waypoints"]
        airport_data["layout"] = layout
    return WorldState(
        time_sec=0,
        tick_sec=data.get("tick_sec", 5),
        airport=AirportState(**airport_data),
        weather=Weather(**data.get("weather", {})),
        rules=RulesConfig(**data.get("rules", {})),
        scoring=ScoringConfig(**scoring_data),
        aircraft=ac,
        events=data.get("events", []),
    )
