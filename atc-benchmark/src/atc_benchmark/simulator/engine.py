from __future__ import annotations

import hashlib
import json
import math
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


def advance(world: WorldState) -> None:
    dt_hr = world.tick_sec / 3600
    for ac in world.aircraft.values():
        if ac.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure", "holding"}:
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
            if ac.role == "arrival" and abs(ac.x_nm) < 1.5 and abs(ac.y_nm) < 1.5 and ac.altitude_ft < 300:
                ac.status = "landed"
                ac.landing_time_sec = world.time_sec + world.tick_sec
                world.airport.runway_occupied_by = ac.callsign
                world.airport.runway_phase = "vacating"
                world.airport.runway_occupied_until_sec = world.time_sec + world.tick_sec + LANDING_RUNWAY_OCCUPANCY_SEC
            if ac.status == "rolling":
                ac.status = "airborne_departure"
                ac.takeoff_time_sec = world.time_sec + world.tick_sec
        if ac.status == "airborne_departure" and (abs(ac.x_nm) > 30 or abs(ac.y_nm) > 30):
            ac.status = "exited_airspace"



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
        ac = world.aircraft[action["aircraft"]]
        t = action["type"]
        if t == "assign_heading":
            ac.heading_deg = action["heading"]
        elif t == "assign_altitude":
            target = action["altitude_ft"]
            ac.target_altitude_ft = target
            ac.vertical_rate_fpm = 0 if target == ac.altitude_ft else 1500 if target > ac.altitude_ft else -1500
        elif t == "assign_speed":
            ac.speed_kt = action["speed_kt"]
        elif t == "clear_to_land":
            ac.clearance = "cleared_to_land"
            ac.status = "on_final"
        elif t == "clear_for_takeoff":
            ac.clearance = "cleared_for_takeoff"
            ac.status = "rolling"
            if ac.ready_time_sec is None:
                ac.ready_time_sec = world.time_sec
            world.airport.runway_occupied_by = ac.callsign
            world.airport.runway_phase = "takeoff_roll"
            world.airport.runway_occupied_until_sec = world.time_sec + TAKEOFF_RUNWAY_OCCUPANCY_SEC
            if ac.callsign in world.airport.departure_queue:
                world.airport.departure_queue.remove(ac.callsign)
        elif t == "go_around":
            ac.status = "go_around"
            ac.vertical_rate_fpm = 1200
            go_arounds += 1
        elif t in {"hold_short", "hold_position"}:
            ac.speed_kt = 0
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
        elif t == "no_op":
            continue
    return {"go_around_count": go_arounds}


def apply_events(world: WorldState) -> list[dict]:
    triggered: list[dict] = []
    for event in world.events:
        if event.get("applied"):
            continue
        if event.get("time_sec") != world.time_sec:
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
        triggered.append({k: v for k, v in event.items() if k != "applied"})
    return triggered


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


def run(world: WorldState, agent, max_ticks: int, trace_path: Path, manifest: dict | None = None) -> dict:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_count = 0
    malformed_agent_outputs_count = 0
    instructions = 0
    loss_sep_count = 0
    min_h = float("inf")
    min_v = float("inf")
    conflict_predicted_times: list[int] = []
    go_around_count = 0
    latest_wind_change_sec: int | None = None
    wind_response_latency_sec: int | None = None
    unsafe_clearances_after_wind_change = 0
    emergency_priority_compliant_count = 0
    emergency_priority_violation_count = 0
    active_conflicts_count_total = 0
    predicted_conflicts_count_total = 0
    restricted_zone_violation_count = 0
    violated_zone_entries: set[tuple[str, str]] = set()
    lifecycle = ConflictLifecycleTracker()
    cumulative_score_components = {
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
    }
    tick_records: list[dict] = []

    for tick_id in range(max_ticks):
        triggered_events = apply_events(world)
        for event in triggered_events:
            if event.get("type") == "wind_change":
                latest_wind_change_sec = world.time_sec
                wind_response_latency_sec = None
        conflicts = detect_conflicts(world)
        if conflicts:
            loss_sep_count += len(conflicts)
            min_h = min(min_h, min(c["horizontal_nm"] for c in conflicts))
            min_v = min(min_v, min(c["vertical_ft"] for c in conflicts))
        active_conflicts_count_total += len(conflicts)

        predictions = predict_conflicts(world)
        lifecycle.update(predictions, is_action_phase=False)
        conflict_predicted_times.extend(p["in_seconds"] for p in predictions)
        predicted_conflicts_count_total += len(predictions)
        dps = detect_decision_points(world)
        if triggered_events:
            for event in triggered_events:
                dps.append({"type": "event", "event": event})

        actions: list[dict] = []
        obs: dict | None = None
        invalid: list[dict] = []
        agent_exception: dict | None = None
        trigger_context = _build_trigger_context(world, dps, triggered_events)
        if world.rules.debug_require_trigger_provenance and not _validate_trigger_context(trigger_context):
            raise ValueError("Missing trigger provenance for invocation in debug mode")

        if dps:
            obs = {"time_sec": world.time_sec, "decision_points": dps, "snapshot": world.snapshot(), "trigger_context": trigger_context}
            try:
                agent_output = agent.act(obs)
            except Exception as exc:  # noqa: BLE001
                malformed = [{"action": None, "reason": "agent_exception", "exception_type": type(exc).__name__, "exception_message": str(exc)}]
                raw_actions: list[dict] = []
                agent_exception = {"type": type(exc).__name__, "message": str(exc)}
            else:
                raw_actions, malformed = extract_actions(agent_output)
            actions = raw_actions
            instructions += len(actions)
            emergency_active = any(
                ac.role == "arrival" and ac.emergency and ac.status != "landed" for ac in world.aircraft.values()
            )
            if emergency_active:
                emergency_callsigns = {
                    ac.callsign for ac in world.aircraft.values() if ac.role == "arrival" and ac.emergency and ac.status != "landed"
                }
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if action.get("type") == "clear_for_takeoff":
                        emergency_priority_violation_count += 1
                    elif action.get("type") == "clear_to_land":
                        if action.get("aircraft") in emergency_callsigns:
                            emergency_priority_compliant_count += 1
                        else:
                            emergency_priority_violation_count += 1
            valid, invalid = validate_actions(world, actions)
            invalid = malformed + invalid
            malformed_agent_outputs_count += len(malformed)
            invalid_count += len(invalid)
            effects = apply_actions(world, valid)
            if latest_wind_change_sec is not None and not _runway_is_wind_compliant(world):
                unsafe_clearances_after_wind_change += sum(1 for a in valid if a["type"] in {"clear_to_land", "clear_for_takeoff"})
            go_around_count += effects["go_around_count"]
            after_predictions = predict_conflicts(world)
            lifecycle.update(after_predictions, is_action_phase=True)

        if latest_wind_change_sec is not None and wind_response_latency_sec is None and _runway_is_wind_compliant(world):
            wind_response_latency_sec = world.time_sec - latest_wind_change_sec

        explanation = _build_tick_explanation(
            tick_id=tick_id,
            world=world,
            dps=dps,
            triggered_events=triggered_events,
            trigger_context=trigger_context,
            actions=actions,
            conflicts=conflicts,
            invalid=invalid,
            cumulative_score_components=cumulative_score_components,
        )
        tick_records.append(
            {
                "time": world.time_sec,
                "triggered_events": triggered_events,
                "decision_points": dps,
                "observation": obs,
                "agent_exception": agent_exception,
                "actions": actions,
                "invalid_actions": invalid,
                "conflicts": conflicts,
                "predicted_conflicts": predictions,
                "state": world.snapshot(),
                "tick_explanation_obj": explanation,
            }
        )

        if world.airport.runway_occupied_by and all(a.status in {"landed", "exited_airspace"} for a in world.aircraft.values()):
            break
        prior_positions = {k: (a.x_nm, a.y_nm) for k, a in world.aircraft.items()}
        advance(world)
        restricted_events = _detect_restricted_zone_crossings(world, prior_positions, violated_zone_entries)
        restricted_zone_violation_count += len(restricted_events)
        tick_records[-1]["triggered_events"].extend(restricted_events)
        world.time_sec += world.tick_sec
        if world.airport.runway_occupied_until_sec is not None and world.time_sec >= world.airport.runway_occupied_until_sec:
            world.airport.runway_occupied_by = None
            world.airport.runway_phase = None
            world.airport.runway_occupied_until_sec = None

    _finalize_tick_outcomes(world, tick_records)
    with trace_path.open("w", encoding="utf-8") as f:
        for tick_record in tick_records:
            explanation = tick_record.pop("tick_explanation_obj")
            event = {**tick_record, "tick_explanation": tick_explanation_to_dict(explanation)}
            f.write(json.dumps(event) + "\n")

    return _build_score_result(
        world,
        lifecycle,
        loss_sep_count=loss_sep_count,
        min_h=min_h,
        min_v=min_v,
        invalid_count=invalid_count,
        malformed_agent_outputs_count=malformed_agent_outputs_count,
        instructions=instructions,
        conflict_predicted_times=conflict_predicted_times,
        go_around_count=go_around_count,
        wind_response_latency_sec=wind_response_latency_sec,
        unsafe_clearances_after_wind_change=unsafe_clearances_after_wind_change,
        emergency_priority_compliant_count=emergency_priority_compliant_count,
        emergency_priority_violation_count=emergency_priority_violation_count,
        active_conflicts_count_total=active_conflicts_count_total,
        predicted_conflicts_count_total=predicted_conflicts_count_total,
        manifest=manifest,
        restricted_zone_violation_count=restricted_zone_violation_count,
    )


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
