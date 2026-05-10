from __future__ import annotations

import json
import math
from pathlib import Path

from .conflict_detection import detect_conflicts, predict_conflicts
from .decision_points import detect_decision_points
from .models import Aircraft, AirportState, RulesConfig, Weather, WorldState
from .validator import validate_actions


def advance(world: WorldState) -> None:
    dt_hr = world.tick_sec / 3600
    for ac in world.aircraft.values():
        if ac.status in {"airborne", "on_final", "go_around", "rolling", "airborne_departure"}:
            rad = math.radians(ac.heading_deg)
            ac.x_nm += math.sin(rad) * ac.speed_kt * dt_hr
            ac.y_nm += math.cos(rad) * ac.speed_kt * dt_hr
            ac.altitude_ft += ac.vertical_rate_fpm * (world.tick_sec / 60)
            if ac.role == "arrival" and abs(ac.x_nm) < 1.5 and abs(ac.y_nm) < 1.5 and ac.altitude_ft < 300:
                ac.status = "landed"
                ac.landing_time_sec = world.time_sec + world.tick_sec
                world.airport.runway_occupied_by = ac.callsign
            if ac.status == "rolling":
                ac.status = "airborne_departure"
                ac.takeoff_time_sec = world.time_sec + world.tick_sec


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


def run(world: WorldState, agent, max_ticks: int, trace_path: Path) -> dict:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_count = 0
    instructions = 0
    loss_sep_count = 0
    min_h = float("inf")
    min_v = float("inf")
    conflict_resolved_count = 0
    prior_predicted_ids: set[str] = set()
    conflict_predicted_times: list[int] = []
    new_conflicts_created_by_action = 0
    go_around_count = 0

    with trace_path.open("w", encoding="utf-8") as f:
        for _ in range(max_ticks):
            triggered_events = apply_events(world)
            conflicts = detect_conflicts(world)
            active_pairs = {c["id"] for c in conflicts}

            if conflicts:
                loss_sep_count += len(conflicts)
                min_h = min(min_h, min(c["horizontal_nm"] for c in conflicts))
                min_v = min(min_v, min(c["vertical_ft"] for c in conflicts))

            predictions = predict_conflicts(world)
            conflict_predicted_times.extend(p["in_seconds"] for p in predictions)
            dps = detect_decision_points(world)
            if triggered_events:
                for event in triggered_events:
                    dps.append({"type": "event", "event": event})

            actions = []
            obs = None
            invalid = []
            if dps:
                obs = {"time_sec": world.time_sec, "decision_points": dps, "snapshot": world.snapshot()}
                actions = agent.act(obs).get("actions", [])
                instructions += len(actions)
                valid, invalid = validate_actions(world, actions)
                invalid_count += len(invalid)
                before_predictions = {p["id"] for p in predictions}
                effects = apply_actions(world, valid)
                go_around_count += effects["go_around_count"]
                after_predictions = {p["id"] for p in predict_conflicts(world)}
                conflict_resolved_count += len(before_predictions - after_predictions)
                new_conflicts_created_by_action += len(after_predictions - before_predictions)

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
            if world.airport.runway_occupied_by:
                world.airport.runway_occupied_by = None

    arrivals = [a for a in world.aircraft.values() if a.role == "arrival"]
    departures = [a for a in world.aircraft.values() if a.role == "departure"]
    for a in departures:
        if a.status in {"airborne_departure"} and (abs(a.x_nm) > 30 or abs(a.y_nm) > 30):
            a.status = "exited_airspace"
    landings = sum(1 for a in arrivals if a.status == "landed")
    departures_ok = sum(1 for a in departures if a.status in {"airborne_departure", "exited_airspace", "landed"})
    arrival_delay = sum(max(0, (a.landing_time_sec or world.time_sec) - a.ideal_landing_time_sec) for a in arrivals if a.ideal_landing_time_sec is not None)
    departure_delay = sum(max(0, (a.takeoff_time_sec or world.time_sec) - a.ideal_takeoff_time_sec) for a in departures if a.ideal_takeoff_time_sec is not None)
    emergency_handled_count = sum(1 for a in arrivals if a.emergency and a.status == "landed")

    score = max(0, 100 - loss_sep_count * 20 - invalid_count * 5 + landings * 3 + departures_ok * 2)
    return {
        "score": score,
        "safety": {"loss_of_separation": loss_sep_count, "min_horizontal_nm": min_h if min_h < float("inf") else None, "min_vertical_ft": min_v if min_v < float("inf") else None},
        "efficiency": {"successful_landings": landings, "successful_departures": departures_ok, "arrival_delay": arrival_delay, "departure_delay": departure_delay},
        "control_quality": {"instructions_issued": instructions, "invalid_commands": invalid_count},
        "metrics": {
            "conflict_predicted_time": min(conflict_predicted_times) if conflict_predicted_times else None,
            "conflict_resolved_count": conflict_resolved_count,
            "new_conflicts_created_by_action": new_conflicts_created_by_action,
            "go_around_count": go_around_count,
            "emergency_handled_count": emergency_handled_count,
        },
    }


def load_world(path: Path) -> WorldState:
    data = json.loads(path.read_text())
    ac = {a["callsign"]: Aircraft(**a) for a in data["aircraft"]}
    for aircraft in ac.values():
        if aircraft.role == "departure" and aircraft.status == "departed":
            aircraft.status = "airborne_departure"
        if aircraft.role == "departure" and aircraft.status == "waiting_departure" and aircraft.ready_time_sec is None:
            aircraft.ready_time_sec = 0
    return WorldState(
        time_sec=0,
        tick_sec=data.get("tick_sec", 5),
        airport=AirportState(**data["airport"]),
        weather=Weather(**data.get("weather", {})),
        rules=RulesConfig(**data.get("rules", {})),
        aircraft=ac,
        events=data.get("events", []),
    )
