from __future__ import annotations

import json
import math
from pathlib import Path

from .conflict_detection import detect_conflicts
from .decision_points import detect_decision_points
from .models import Aircraft, AirportState, RulesConfig, Weather, WorldState
from .validator import validate_actions


def advance(world: WorldState) -> None:
    dt_hr = world.tick_sec / 3600
    for ac in world.aircraft.values():
        if ac.status in {"airborne", "on_final", "go_around", "departed"}:
            rad = math.radians(ac.heading_deg)
            ac.x_nm += math.sin(rad) * ac.speed_kt * dt_hr
            ac.y_nm += math.cos(rad) * ac.speed_kt * dt_hr
            ac.altitude_ft += ac.vertical_rate_fpm * (world.tick_sec / 60)
            if ac.role == "arrival" and abs(ac.x_nm) < 1.5 and abs(ac.y_nm) < 1.5 and ac.altitude_ft < 300:
                ac.status = "landed"
                world.airport.runway_occupied_by = ac.callsign


def apply_actions(world: WorldState, actions: list[dict]) -> None:
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
            ac.status = "departed"
            world.airport.runway_occupied_by = ac.callsign
        elif t == "go_around":
            ac.status = "go_around"
            ac.vertical_rate_fpm = 1200
        elif t in {"hold_short", "hold_position"}:
            ac.speed_kt = 0


def run(world: WorldState, agent, max_ticks: int, trace_path: Path) -> dict:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_count = 0
    instructions = 0
    loss_sep_count = 0
    min_h = float("inf")
    min_v = float("inf")

    with trace_path.open("w", encoding="utf-8") as f:
        for _ in range(max_ticks):
            conflicts = detect_conflicts(world)
            if conflicts:
                loss_sep_count += len(conflicts)
                min_h = min(min_h, min(c["horizontal_nm"] for c in conflicts))
                min_v = min(min_v, min(c["vertical_ft"] for c in conflicts))

            dps = detect_decision_points(world)
            actions = []
            obs = None
            invalid = []
            if dps:
                obs = {"time_sec": world.time_sec, "decision_points": dps, "snapshot": world.snapshot()}
                actions = agent.act(obs).get("actions", [])
                instructions += len(actions)
                valid, invalid = validate_actions(world, actions)
                invalid_count += len(invalid)
                apply_actions(world, valid)

            event = {
                "time": world.time_sec,
                "decision_points": dps,
                "observation": obs,
                "actions": actions,
                "invalid_actions": invalid,
                "conflicts": conflicts,
                "state": world.snapshot(),
            }
            f.write(json.dumps(event) + "\n")

            if world.airport.runway_occupied_by and all(a.status in {"landed", "departed"} for a in world.aircraft.values()):
                break
            advance(world)
            world.time_sec += world.tick_sec
            if world.airport.runway_occupied_by:
                world.airport.runway_occupied_by = None

    landings = sum(1 for a in world.aircraft.values() if a.status == "landed")
    departures = sum(1 for a in world.aircraft.values() if a.status == "departed")
    score = max(0, 100 - loss_sep_count * 20 - invalid_count * 5 + landings * 3 + departures * 2)
    return {
        "score": score,
        "safety": {"loss_of_separation": loss_sep_count, "min_horizontal_nm": min_h if min_h < float("inf") else None, "min_vertical_ft": min_v if min_v < float("inf") else None},
        "efficiency": {"successful_landings": landings, "successful_departures": departures},
        "control_quality": {"instructions_issued": instructions, "invalid_commands": invalid_count},
    }


def load_world(path: Path) -> WorldState:
    data = json.loads(path.read_text())
    ac = {a["callsign"]: Aircraft(**a) for a in data["aircraft"]}
    return WorldState(
        time_sec=0,
        tick_sec=data.get("tick_sec", 5),
        airport=AirportState(**data["airport"]),
        weather=Weather(**data.get("weather", {})),
        rules=RulesConfig(**data.get("rules", {})),
        aircraft=ac,
    )
