from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from atc_benchmark.paths import resolve_scenario_path, scenarios_dir
from atc_benchmark.runner.game_session import build_mission_debrief, star_rating
from atc_benchmark.runner.live_commands import handle_ws_envelope
from atc_benchmark.runner.run_scenario import build_manifest
from atc_benchmark.server import LiveTransportServer, create_live_asgi_app
from atc_benchmark.simulator.conflict_detection import detect_conflicts, predict_conflicts
from atc_benchmark.simulator.decision_points import detect_decision_points
from atc_benchmark.simulator.engine import SimulationStepper, load_world
from atc_benchmark.simulator.models import WorldState


def _prettify_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def list_levels(scenarios_root: Path | None = None) -> list[dict[str, Any]]:
    """Build the level catalog from scenario files and their metadata."""
    root = scenarios_root or scenarios_dir()
    levels: list[dict[str, Any]] = []
    for scenario_path in sorted(root.glob("*.json")):
        try:
            doc = json.loads(scenario_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        metadata = doc.get("scenario_metadata", {}) if isinstance(doc, dict) else {}
        aircraft = doc.get("aircraft", []) if isinstance(doc, dict) else []
        arrivals = sum(1 for a in aircraft if isinstance(a, dict) and a.get("role") == "arrival")
        departures = sum(1 for a in aircraft if isinstance(a, dict) and a.get("role") == "departure")
        levels.append(
            {
                "id": scenario_path.name,
                "name": _prettify_stem(scenario_path.stem),
                "difficulty_tier": metadata.get("difficulty_tier"),
                "tags": metadata.get("tags", []),
                "intended_stressors": metadata.get("intended_stressors", []),
                "aircraft_count": len(aircraft),
                "arrivals": arrivals,
                "departures": departures,
                "has_events": bool(doc.get("events")) if isinstance(doc, dict) else False,
            }
        )
    return levels


def _any_aircraft_lost(world: WorldState) -> bool:
    return any(ac.status == "terminal_failure" for ac in world.aircraft.values())


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
    """State-only preview published when a level starts, before the clock advances."""
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


class LiveGameServer:
    """Owns the live session: level lifecycle, command handling, tick loop."""

    def __init__(
        self,
        *,
        scenarios_root: Path | None = None,
        max_ticks: int = 300,
        tick_interval_sec: float = 1.0,
        output_root: Path = Path("outputs/live"),
    ) -> None:
        self.session_id = uuid4().hex
        self.scenarios_root = scenarios_root or scenarios_dir()
        self.max_ticks = max_ticks
        self.tick_interval_sec = tick_interval_sec
        self.output_root = output_root
        self.lock = asyncio.Lock()
        self.transport = LiveTransportServer()
        self.levels = list_levels(self.scenarios_root)
        self.stepper: SimulationStepper | None = None
        self.scenario: Path | None = None
        self.manifest: dict[str, Any] | None = None
        self.level_active = False
        self.level_serial = 0
        self.paused = False
        self.ended = False

    def _level_descriptor(self, scenario: Path) -> dict[str, Any]:
        for level in self.levels:
            if level["id"] == scenario.name:
                return level
        return {"id": scenario.name, "name": _prettify_stem(scenario.stem)}

    def _resolve_level(self, level_id: Any) -> Path | None:
        """Resolve a requested level strictly against the catalog (no traversal)."""
        if not isinstance(level_id, str):
            return None
        for level in self.levels:
            if level["id"] == level_id:
                return self.scenarios_root / level_id
        return None

    async def start_level(self, scenario: Path) -> dict[str, Any]:
        """Load a scenario and begin ticking. Caller must hold the lock."""
        world = load_world(scenario)
        self.stepper = SimulationStepper(world)
        self.scenario = scenario
        self.manifest = build_manifest(scenario, world, "live", self.max_ticks)
        self.level_active = True
        self.level_serial += 1
        self.paused = False
        self.ended = False
        descriptor = self._level_descriptor(scenario)
        await self.transport.publish_envelope(
            {"type": "level_started", "session_id": self.session_id, "level": descriptor}
        )
        await self.transport.publish_envelope(
            {"type": "tick", "session_id": self.session_id, "tick": _initial_tick_event(world, session_id=self.session_id)}
        )
        return descriptor

    async def finish_level(self, outcome: str, *, serial: int | None = None) -> None:
        async with self.lock:
            if serial is not None and serial != self.level_serial:
                return
            if not self.level_active or self.stepper is None or self.scenario is None:
                return
            stepper = self.stepper
            stepper.finalize()
            score = stepper.build_score(self.manifest)
            score.setdefault("metrics", {})["session_outcome"] = outcome
            metadata = (self.manifest or {}).get("scenario_metadata", {})
            crashed = outcome == "aircraft_lost"
            abandoned = outcome == "ended_by_user"
            override = "crashed" if crashed else "abandoned" if abandoned else None
            debrief = build_mission_debrief(score, outcome_override=override)
            stars = 0 if (crashed or abandoned) else star_rating(score["score"], metadata)
            output_dir = self.output_root / f"{self.scenario.stem}-{self.session_id[:8]}-{self.level_serial}"
            artifacts = _write_session_artifacts(stepper, score, output_dir)
            scenario_id = self.scenario.name
            self.level_active = False
            self.ended = False
        await self.transport.publish_envelope(
            {
                "type": "level_complete",
                "session_id": self.session_id,
                "scenario": scenario_id,
                "outcome": outcome,
                "debrief": debrief.to_dict(),
                "stars": stars,
                "score": score,
                "artifacts": artifacts,
            }
        )

    async def publish_control_status(self, status: str) -> None:
        await self.transport.publish_envelope(
            {
                "type": "control_status",
                "session_id": self.session_id,
                "status": status,
                "paused": self.paused,
                "tick_id": self.stepper.tick_id if self.stepper else 0,
            }
        )

    async def on_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "session_id": payload.get("session_id") or self.session_id}
        envelope_type = payload.get("type")
        control_status: str | None = None
        async with self.lock:
            if envelope_type == "list_levels":
                response: dict[str, Any] = {
                    "type": "level_list",
                    "session_id": self.session_id,
                    "levels": self.levels,
                }
            elif envelope_type == "start_level":
                scenario = self._resolve_level(payload.get("scenario"))
                if scenario is None:
                    response = {"type": "control_ack", "status": "unknown_level", "ok": False, "session_id": self.session_id}
                else:
                    descriptor = await self.start_level(scenario)
                    response = {
                        "type": "control_ack",
                        "status": "level_started",
                        "ok": True,
                        "session_id": self.session_id,
                        "level": descriptor,
                    }
                    control_status = "running"
            elif envelope_type == "pause":
                self.paused = True
                response = {"type": "control_ack", "status": "paused", "ok": True, "session_id": self.session_id}
                control_status = "paused"
            elif envelope_type == "resume":
                self.paused = False
                response = {"type": "control_ack", "status": "running", "ok": True, "session_id": self.session_id}
                control_status = "running"
            elif envelope_type == "reset":
                if self.scenario is None:
                    response = {"type": "control_ack", "status": "no_level", "ok": False, "session_id": self.session_id}
                else:
                    await self.start_level(self.scenario)
                    response = {"type": "control_ack", "status": "reset", "ok": True, "session_id": self.session_id}
                    control_status = "reset"
            elif envelope_type == "end_session":
                self.ended = True
                response = {"type": "control_ack", "status": "ended", "ok": True, "session_id": self.session_id}
                control_status = "ended"
            else:
                if self.stepper is None or not self.level_active:
                    response = {"type": "command_ack", "ok": False, "status": "nack", "reason": "no_active_level"}
                else:
                    response = handle_ws_envelope(self.stepper, payload)
        if control_status is not None:
            await self.publish_control_status(control_status)
        return response

    async def run_loop(self) -> None:
        while True:
            if not self.level_active:
                await asyncio.sleep(0.1)
                continue
            if self.ended:
                await self.finish_level("ended_by_user")
                continue
            if self.paused:
                await asyncio.sleep(0.05)
                continue

            outcome: str | None = None
            event: dict[str, Any] | None = None
            serial = None
            async with self.lock:
                if not self.level_active or self.stepper is None:
                    continue
                serial = self.level_serial
                stepper = self.stepper
                if stepper.tick_id >= self.max_ticks:
                    outcome = "max_ticks_reached"
                else:
                    stepper.begin_tick()
                    record = stepper.finish_tick()
                    event = stepper.record_to_event(record)
                    event["tick_id"] = stepper.tick_id - 1
                    event["session_id"] = self.session_id
                    event["running_score"] = _running_score_summary(stepper.build_score())
                    if _any_aircraft_lost(stepper.world):
                        outcome = "aircraft_lost"
                    elif stepper.is_complete():
                        outcome = "all_traffic_handled"

            if event is not None:
                await self.transport.publish_envelope({"type": "tick", "session_id": self.session_id, "tick": event})
            if outcome is not None:
                await self.finish_level(outcome, serial=serial)
                continue
            await asyncio.sleep(self.tick_interval_sec)


async def serve_live(
    *,
    scenario: Path | None,
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

    game = LiveGameServer(
        max_ticks=max_ticks,
        tick_interval_sec=tick_interval_sec,
        output_root=output_dir,
    )
    if scenario is not None:
        async with game.lock:
            await game.start_level(scenario)

    app = create_live_asgi_app(game.transport, on_command=game.on_command)
    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="off")
    server = uvicorn.Server(config)
    simulation_task = asyncio.create_task(game.run_loop())
    server_task = asyncio.create_task(server.serve())

    print(
        json.dumps(
            {
                "live_url": f"ws://{host}:{port}/live",
                "session_id": game.session_id,
                "scenario": scenario.name if scenario else None,
                "levels": len(game.levels),
                "output_dir": str(output_dir),
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
    parser.add_argument("scenario", nargs="?", default=None, help="Optional scenario to start immediately; otherwise the server waits for start_level.")
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
    scenario = resolve_scenario_path(Path(args.scenario)) if args.scenario else None
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
