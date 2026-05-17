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
    world: WorldState,
    live_server: LiveTransportServer,
    lock: asyncio.Lock,
    session_id: str,
    max_ticks: int,
    tick_interval_sec: float,
    manifest: dict[str, Any],
) -> None:
    for tick_id in range(max_ticks):
        async with lock:
            triggered_events = apply_events(world)
            event = _tick_event(world, tick_id, session_id=session_id, triggered_events=triggered_events)
            complete = _level_complete(world)
            _advance_world(world)

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

    async def on_command(payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "session_id": payload.get("session_id") or session_id}
        async with lock:
            return handle_ws_envelope(world, payload)

    app = create_live_asgi_app(live_server, on_command=on_command)
    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="off")
    server = uvicorn.Server(config)
    simulation_task = asyncio.create_task(
        _run_simulation(
            world=world,
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
