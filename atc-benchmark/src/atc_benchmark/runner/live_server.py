from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.runner.live_commands import handle_ws_envelope
from atc_benchmark.runner.run_scenario import build_manifest
from atc_benchmark.server import LiveTransportServer, create_live_asgi_app
from atc_benchmark.simulator.conflict_detection import detect_conflicts, predict_conflicts
from atc_benchmark.simulator.decision_points import detect_decision_points
from atc_benchmark.simulator.engine import advance, apply_events, load_world
from atc_benchmark.simulator.models import WorldState


def _tick_event(
    world: WorldState,
    tick_id: int,
    *,
    session_id: str,
    triggered_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "tick_id": tick_id,
        "time": world.time_sec,
        "session_id": session_id,
        "triggered_events": triggered_events,
        "decision_points": detect_decision_points(world),
        "observation": None,
        "agent_exception": None,
        "actions": [],
        "invalid_actions": [],
        "conflicts": detect_conflicts(world),
        "predicted_conflicts": predict_conflicts(world),
        "state": world.snapshot(),
    }


def _advance_world(world: WorldState) -> None:
    advance(world)
    world.time_sec += world.tick_sec
    if world.airport.runway_occupied_until_sec is not None and world.time_sec >= world.airport.runway_occupied_until_sec:
        world.airport.runway_occupied_by = None
        world.airport.runway_phase = None
        world.airport.runway_occupied_until_sec = None


def _level_complete(world: WorldState) -> bool:
    return bool(world.airport.runway_occupied_by) and all(
        aircraft.status in {"landed", "exited_airspace"} for aircraft in world.aircraft.values()
    )


async def _run_simulation(
    *,
    runtime: dict[str, Any],
    live_server: LiveTransportServer,
    lock: asyncio.Lock,
    session_id: str,
    max_ticks: int,
    tick_interval_sec: float,
    manifest: dict[str, Any],
) -> None:
    while runtime["tick_id"] < max_ticks:
        if runtime.get("ended"):
            await live_server.publish_envelope({"type": "level_complete", "session_id": session_id, "score": None})
            return
        if runtime.get("paused"):
            await asyncio.sleep(0.05)
            continue

        async with lock:
            world = runtime["world"]
            tick_id = runtime["tick_id"]
            triggered_events = apply_events(world)
            event = _tick_event(world, tick_id, session_id=session_id, triggered_events=triggered_events)
            event["actions"] = runtime["pending_actions"]
            event["invalid_actions"] = runtime["pending_invalid_actions"]
            runtime["pending_actions"] = []
            runtime["pending_invalid_actions"] = []
            complete = _level_complete(world)
            _advance_world(world)
            runtime["tick_id"] += 1

        await live_server.publish_envelope({"type": "tick", "session_id": session_id, "tick": event})
        if complete:
            await live_server.publish_envelope({"type": "level_complete", "session_id": session_id, "score": None})
            return
        await asyncio.sleep(tick_interval_sec)

    await live_server.publish_envelope(
        {
            "type": "level_complete",
            "session_id": session_id,
            "score": {"score": None, "run_manifest": manifest, "metrics": {"max_ticks_reached": max_ticks}},
        }
    )


async def serve_live(
    *,
    scenario: Path,
    host: str,
    port: int,
    max_ticks: int,
    tick_interval_sec: float,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Live mode requires uvicorn. Install it with: pip install -e \".[dev,live]\""
        ) from exc

    world = load_world(scenario)
    session_id = uuid4().hex
    manifest = build_manifest(scenario, world, "live", max_ticks)
    lock = asyncio.Lock()
    live_server = LiveTransportServer()
    runtime: dict[str, Any] = {
        "world": world,
        "tick_id": 0,
        "paused": False,
        "ended": False,
        "pending_actions": [],
        "pending_invalid_actions": [],
    }

    async def publish_control_status(status: str) -> None:
        await live_server.publish_envelope(
            {
                "type": "control_status",
                "session_id": session_id,
                "status": status,
                "paused": runtime["paused"],
                "tick_id": runtime["tick_id"],
            }
        )

    async def on_command(payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "session_id": payload.get("session_id") or session_id}
        envelope_type = payload.get("type")
        async with lock:
            if envelope_type == "pause":
                runtime["paused"] = True
                response = {"type": "control_ack", "status": "paused", "ok": True, "session_id": session_id}
            elif envelope_type == "resume":
                runtime["paused"] = False
                response = {"type": "control_ack", "status": "running", "ok": True, "session_id": session_id}
            elif envelope_type == "reset":
                runtime["world"] = load_world(scenario)
                runtime["tick_id"] = 0
                runtime["paused"] = False
                runtime["ended"] = False
                runtime["pending_actions"] = []
                runtime["pending_invalid_actions"] = []
                event = _tick_event(runtime["world"], 0, session_id=session_id, triggered_events=[])
                await live_server.publish_envelope({"type": "tick", "session_id": session_id, "tick": event})
                runtime["tick_id"] = 1
                response = {"type": "control_ack", "status": "reset", "ok": True, "session_id": session_id}
            elif envelope_type == "end_session":
                runtime["ended"] = True
                response = {"type": "control_ack", "status": "ended", "ok": True, "session_id": session_id}
            else:
                response = handle_ws_envelope(runtime["world"], payload)
                details = response.get("details", {})
                if response.get("ok"):
                    runtime["pending_actions"].append(details.get("accepted_action", payload.get("command")))
                else:
                    runtime["pending_invalid_actions"].append(
                        {"action": details.get("rejected_action", payload.get("command")), "reason": response.get("reason")}
                    )
        if envelope_type in {"pause", "resume", "reset", "end_session"}:
            await publish_control_status(str(response["status"]))
        return response

    app = create_live_asgi_app(live_server, on_command=on_command)
    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="off")
    server = uvicorn.Server(config)
    simulation_task = asyncio.create_task(
        _run_simulation(
            runtime=runtime,
            live_server=live_server,
            lock=lock,
            session_id=session_id,
            max_ticks=max_ticks,
            tick_interval_sec=tick_interval_sec,
            manifest=manifest,
        )
    )
    server_task = asyncio.create_task(server.serve())

    print(json.dumps({"live_url": f"ws://{host}:{port}/live", "session_id": session_id, "scenario": scenario.name}, indent=2))
    try:
        done, _pending = await asyncio.wait({simulation_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        server.should_exit = True
        await server_task
    finally:
        for task in (simulation_task, server_task):
            if not task.done():
                task.cancel()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-ticks", type=int, default=300)
    parser.add_argument(
        "--tick-interval-sec",
        type=float,
        default=1.0,
        help="Wall-clock seconds between simulator ticks.",
    )
    args = parser.parse_args()
    scenario = resolve_scenario_path(Path(args.scenario))
    asyncio.run(
        serve_live(
            scenario=scenario,
            host=args.host,
            port=args.port,
            max_ticks=args.max_ticks,
            tick_interval_sec=args.tick_interval_sec,
        )
    )


if __name__ == "__main__":
    main()
