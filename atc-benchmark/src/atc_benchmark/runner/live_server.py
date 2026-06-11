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
from atc_benchmark.simulator.engine import SimulationStepper, load_world
from atc_benchmark.simulator.models import WorldState


def _running_score_summary(score: dict[str, Any]) -> dict[str, Any]:
    """Trim a full score result to the fields a live HUD needs every tick."""
    return {
        "score": score["score"],
        "score_breakdown": score["score_breakdown"],
        "safety": score["safety"],
        "efficiency": score["efficiency"],
        "control_quality": score["control_quality"],
    }


def _initial_tick_event(world: WorldState, *, session_id: str) -> dict[str, Any]:
    """State-only preview published after a reset, before the clock advances."""
    conflicts = detect_conflicts(world)
    predictions = predict_conflicts(world)
    return {
        "tick_id": 0,
        "time": world.time_sec,
        "session_id": session_id,
        "triggered_events": [],
        "decision_points": detect_decision_points(world, conflicts=conflicts, predictions=predictions),
        "observation": None,
        "agent_exception": None,
        "actions": [],
        "invalid_actions": [],
        "conflicts": conflicts,
        "predicted_conflicts": predictions,
        "state": world.snapshot(),
    }


def _write_session_artifacts(stepper: SimulationStepper, score: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / "trace.jsonl"
    score_path = output_dir / "score.json"
    stepper.write_trace(trace_path)
    score_path.write_text(json.dumps(score, indent=2))
    return {"trace": str(trace_path), "score": str(score_path)}


async def _run_simulation(
    *,
    runtime: dict[str, Any],
    live_server: LiveTransportServer,
    lock: asyncio.Lock,
    session_id: str,
    max_ticks: int,
    tick_interval_sec: float,
    manifest: dict[str, Any],
    output_dir: Path,
) -> None:
    async def finish_session(outcome: str) -> None:
        async with lock:
            stepper = runtime["stepper"]
            stepper.finalize()
            score = stepper.build_score(manifest)
            score.setdefault("metrics", {})["session_outcome"] = outcome
            artifacts = _write_session_artifacts(stepper, score, output_dir)
        await live_server.publish_envelope(
            {
                "type": "level_complete",
                "session_id": session_id,
                "outcome": outcome,
                "score": score,
                "artifacts": artifacts,
            }
        )

    while runtime["stepper"].tick_id < max_ticks:
        if runtime.get("ended"):
            await finish_session("ended_by_user")
            return
        if runtime.get("paused"):
            await asyncio.sleep(0.05)
            continue

        async with lock:
            stepper = runtime["stepper"]
            stepper.begin_tick()
            record = stepper.finish_tick()
            event = stepper.record_to_event(record)
            event["tick_id"] = stepper.tick_id - 1
            event["session_id"] = session_id
            event["running_score"] = _running_score_summary(stepper.build_score())
            complete = stepper.is_complete()

        await live_server.publish_envelope({"type": "tick", "session_id": session_id, "tick": event})
        if complete:
            await finish_session("all_traffic_handled")
            return
        await asyncio.sleep(tick_interval_sec)

    await finish_session("max_ticks_reached")


async def serve_live(
    *,
    scenario: Path,
    host: str,
    port: int,
    max_ticks: int,
    tick_interval_sec: float,
    output_dir: Path,
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
    session_output_dir = output_dir / f"{scenario.stem}-{session_id[:8]}"
    lock = asyncio.Lock()
    live_server = LiveTransportServer()
    runtime: dict[str, Any] = {
        "stepper": SimulationStepper(world),
        "paused": False,
        "ended": False,
    }

    async def publish_control_status(status: str) -> None:
        await live_server.publish_envelope(
            {
                "type": "control_status",
                "session_id": session_id,
                "status": status,
                "paused": runtime["paused"],
                "tick_id": runtime["stepper"].tick_id,
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
                runtime["stepper"] = SimulationStepper(load_world(scenario))
                runtime["paused"] = False
                runtime["ended"] = False
                event = _initial_tick_event(runtime["stepper"].world, session_id=session_id)
                await live_server.publish_envelope({"type": "tick", "session_id": session_id, "tick": event})
                response = {"type": "control_ack", "status": "reset", "ok": True, "session_id": session_id}
            elif envelope_type == "end_session":
                runtime["ended"] = True
                response = {"type": "control_ack", "status": "ended", "ok": True, "session_id": session_id}
            else:
                response = handle_ws_envelope(runtime["stepper"], payload)
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
            output_dir=session_output_dir,
        )
    )
    server_task = asyncio.create_task(server.serve())

    print(
        json.dumps(
            {
                "live_url": f"ws://{host}:{port}/live",
                "session_id": session_id,
                "scenario": scenario.name,
                "output_dir": str(session_output_dir),
            },
            indent=2,
        )
    )
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
    parser.add_argument(
        "--output-dir",
        default="outputs/live",
        help="Directory for per-session trace and score artifacts.",
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
            output_dir=Path(args.output_dir),
        )
    )


if __name__ == "__main__":
    main()
