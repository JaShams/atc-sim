from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .conflict_detection import detect_conflicts, predict_conflicts
from .decision_points import detect_decision_points
from atc_benchmark import __version__

from atc_benchmark.agents.base import extract_actions
from .models import Aircraft, AirportState, RulesConfig, ScoringConfig, Weather, WorldState
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



def advance(world: WorldState) -> None:
    dt_hr = world.tick_sec / 3600
    for ac in world.aircraft.values():
        if ac.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}:
            rad = math.radians(ac.heading_deg)
            wind_to_deg = (world.weather.wind_dir_deg + 180) % 360
            wind_rad = math.radians(wind_to_deg)
            ground_vx_kt = math.sin(rad) * ac.speed_kt + math.sin(wind_rad) * world.weather.wind_speed_kt
            ground_vy_kt = math.cos(rad) * ac.speed_kt + math.cos(wind_rad) * world.weather.wind_speed_kt
            ac.x_nm += ground_vx_kt * dt_hr
            ac.y_nm += ground_vy_kt * dt_hr
            ac.altitude_ft += ac.vertical_rate_fpm * (world.tick_sec / 60)
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


def apply_actions(world: WorldState, actions: list[dict]) -> dict:
    go_arounds = 0
    for action in actions:
        ac = world.aircraft[action["aircraft"]]
        t = action["type"]
        if t == "assign_heading":
            ac.heading_deg = action["heading"]
        elif t == "assign_altitude":
            target = action["altitude_ft"]
            ac.vertical_rate_fpm = 1500 if target > ac.altitude_ft else -1500
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


def run(world: WorldState, agent, max_ticks: int, trace_path: Path, manifest: dict | None = None) -> dict:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_count = 0
    malformed_agent_outputs_count = 0
    instructions = 0
    loss_sep_count = 0
    min_h = float("inf")
    min_v = float("inf")
    conflict_resolved_count = 0
    conflict_predicted_times: list[int] = []
    secondary_conflicts_created_count = 0
    conflicts_delayed_count = 0
    conflicts_worsened_count = 0
    total_conflict_time_gained_sec = 0.0
    conflict_time_gain_samples = 0
    go_around_count = 0
    latest_wind_change_sec: int | None = None
    wind_change_response_latency_sec: int | None = None
    unsafe_clearances_after_wind_change = 0
    active_conflicts_count_total = 0
    predicted_conflicts_count_total = 0
    conflict_lifecycle_state: dict[str, dict] = {}

    def _update_lifecycle(predictions: list[dict], *, is_action_phase: bool) -> None:
        current_pairs = {p["conflict_pair_id"]: p for p in predictions}
        active_pairs = {pair_id for pair_id, st in conflict_lifecycle_state.items() if st.get("active", False)}

        for pair_id, prediction in current_pairs.items():
            predicted_time = prediction["predicted_time_sec"]
            state = conflict_lifecycle_state.get(pair_id)
            if state is None:
                conflict_lifecycle_state[pair_id] = {
                    "first_predicted_time_sec": predicted_time,
                    "last_predicted_time_sec": predicted_time,
                    "active": True,
                    "events": ["introduced"],
                    "secondary_created_events": 1 if is_action_phase else 0,
                }
                continue

            if not state["active"]:
                state["active"] = True
                state["events"].append("reintroduced")
                if is_action_phase:
                    state["secondary_created_events"] += 1
            elif is_action_phase and predicted_time > state["last_predicted_time_sec"]:
                state["events"].append("delayed")
            elif is_action_phase and predicted_time < state["last_predicted_time_sec"]:
                state["events"].append("worsened")
            state["last_predicted_time_sec"] = predicted_time

        resolved_pairs = active_pairs - set(current_pairs.keys())
        for pair_id in resolved_pairs:
            state = conflict_lifecycle_state[pair_id]
            state["active"] = False
            state["events"].append("resolved")

    with trace_path.open("w", encoding="utf-8") as f:
        for _ in range(max_ticks):
            triggered_events = apply_events(world)
            for event in triggered_events:
                if event.get("type") == "wind_change":
                    latest_wind_change_sec = world.time_sec
                    wind_change_response_latency_sec = None
            conflicts = detect_conflicts(world)
            if conflicts:
                loss_sep_count += len(conflicts)
                min_h = min(min_h, min(c["horizontal_nm"] for c in conflicts))
                min_v = min(min_v, min(c["vertical_ft"] for c in conflicts))
            active_conflicts_count_total += len(conflicts)

            predictions = predict_conflicts(world)
            _update_lifecycle(predictions, is_action_phase=False)
            conflict_predicted_times.extend(p["in_seconds"] for p in predictions)
            predicted_conflicts_count_total += len(predictions)
            dps = detect_decision_points(world)
            if triggered_events:
                for event in triggered_events:
                    dps.append({"type": "event", "event": event})

            actions = []
            obs = None
            invalid = []
            if dps:
                obs = {"time_sec": world.time_sec, "decision_points": dps, "snapshot": world.snapshot()}
                raw_actions, malformed = extract_actions(agent.act(obs))
                actions = raw_actions
                instructions += len(actions)
                valid, invalid = validate_actions(world, actions)
                invalid = malformed + invalid
                malformed_agent_outputs_count += len(malformed)
                invalid_count += len(invalid)
                before_by_pair = {p["conflict_pair_id"]: p for p in predictions}
                effects = apply_actions(world, valid)
                if latest_wind_change_sec is not None and not _runway_is_wind_compliant(world):
                    unsafe_clearances_after_wind_change += sum(1 for a in valid if a["type"] in {"clear_to_land", "clear_for_takeoff"})
                go_around_count += effects["go_around_count"]
                after_predictions = predict_conflicts(world)
                _update_lifecycle(after_predictions, is_action_phase=True)

            if latest_wind_change_sec is not None and wind_change_response_latency_sec is None and _runway_is_wind_compliant(world):
                wind_change_response_latency_sec = world.time_sec - latest_wind_change_sec

            event = {
                "time": world.time_sec,
                "triggered_events": triggered_events,
                "decision_points": dps,
                "observation": obs,
                "actions": actions,
                "invalid_actions": invalid,
                "conflicts": conflicts,
                "predicted_conflicts": predictions,
                "state": world.snapshot(),
            }
            f.write(json.dumps(event) + "\n")

            if world.airport.runway_occupied_by and all(a.status in {"landed", "exited_airspace"} for a in world.aircraft.values()):
                break
            advance(world)
            world.time_sec += world.tick_sec
            if world.airport.runway_occupied_until_sec is not None and world.time_sec >= world.airport.runway_occupied_until_sec:
                world.airport.runway_occupied_by = None
                world.airport.runway_phase = None
                world.airport.runway_occupied_until_sec = None

    for state in conflict_lifecycle_state.values():
        events = state["events"]
        conflict_resolved_count += events.count("resolved")
        secondary_conflicts_created_count += state["secondary_created_events"]
        conflicts_delayed_count += events.count("delayed")
        conflicts_worsened_count += events.count("worsened")
        total_conflict_time_gained_sec += state["last_predicted_time_sec"] - state["first_predicted_time_sec"]
        conflict_time_gain_samples += 1

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
        "base_score": scoring.base_score,
        "loss_of_separation": loss_sep_count * scoring.loss_of_separation_penalty,
        "invalid_command": invalid_count * scoring.invalid_command_penalty,
        "secondary_conflicts_created": secondary_conflicts_created_count * scoring.secondary_conflicts_created_penalty,
        "conflicts_worsened": conflicts_worsened_count * scoring.conflicts_worsened_penalty,
        "conflicts_delayed": conflicts_delayed_count * scoring.conflicts_delayed_reward,
        "conflict_resolved": conflict_resolved_count * scoring.conflict_resolved_reward,
        "arrival_delay_sec": arrival_delay * scoring.arrival_delay_sec_penalty,
        "departure_delay_sec": departure_delay * scoring.departure_delay_sec_penalty,
        "successful_landing": landings * scoring.successful_landing_reward,
        "successful_departure": departures_ok * scoring.successful_departure_reward,
        "emergency_handled": emergency_handled_count * scoring.emergency_handled_reward,
        "emergency_unhandled": emergency_unhandled_count * scoring.emergency_unhandled_penalty,
    }
    raw_score = sum(score_breakdown.values())
    score = max(0.0, raw_score)
    simulated_hours = world.time_sec / 3600 if world.time_sec > 0 else 0.0
    throughput_ops_per_hour = ((landings + departures_ok) / simulated_hours) if simulated_hours > 0 else 0.0
    result = {
        "score": score,
        "score_breakdown": score_breakdown,
        "safety": {"loss_of_separation": loss_sep_count, "min_horizontal_nm": min_h if min_h < float("inf") else None, "min_vertical_ft": min_v if min_v < float("inf") else None},
        "efficiency": {"successful_landings": landings, "successful_departures": departures_ok, "arrival_delay": arrival_delay, "departure_delay": departure_delay},
        "control_quality": {"instructions_issued": instructions, "invalid_commands": invalid_count},
        "metrics": {
            "conflict_predicted_time": min(conflict_predicted_times) if conflict_predicted_times else None,
            "conflict_resolved_count": conflict_resolved_count,
            "conflicts_delayed_count": conflicts_delayed_count,
            "conflicts_worsened_count": conflicts_worsened_count,
            "average_conflict_time_gained_sec": (
                total_conflict_time_gained_sec / conflict_time_gain_samples if conflict_time_gain_samples else None
            ),
            "secondary_conflicts_created_count": secondary_conflicts_created_count,
            "go_around_count": go_around_count,
            "emergency_handled_count": emergency_handled_count,
            "emergency_unhandled_count": emergency_unhandled_count,
            "wind_change_response_latency_sec": wind_change_response_latency_sec,
            "unsafe_clearances_after_wind_change": unsafe_clearances_after_wind_change,
            "runway_unsafe_clearance_count": unsafe_clearances_after_wind_change,
            "malformed_agent_outputs_count": malformed_agent_outputs_count,
            "active_conflicts_count_total": active_conflicts_count_total,
            "predicted_conflicts_count_total": predicted_conflicts_count_total,
            "throughput_ops_per_hour": throughput_ops_per_hour,
        },
    }
    if manifest is not None:
        result["run_manifest"] = manifest
    return result


def scenario_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_world(path: Path) -> WorldState:
    data = json.loads(path.read_text())
    ac = {a["callsign"]: Aircraft(**a) for a in data["aircraft"]}
    for aircraft in ac.values():
        if aircraft.role == "departure" and aircraft.status == "departed":
            aircraft.status = "airborne_departure"
        if aircraft.role == "departure" and aircraft.status == "waiting_departure" and aircraft.ready_time_sec is None:
            aircraft.ready_time_sec = 0
    scoring_data = data.get("scoring", {})
    return WorldState(
        time_sec=0,
        tick_sec=data.get("tick_sec", 5),
        airport=AirportState(**data["airport"]),
        weather=Weather(**data.get("weather", {})),
        rules=RulesConfig(**data.get("rules", {})),
        scoring=ScoringConfig(**scoring_data),
        aircraft=ac,
        events=data.get("events", []),
    )
