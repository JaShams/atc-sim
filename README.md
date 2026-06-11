# ATC Sim

ATC Sim contains a deterministic air-traffic-control benchmark package and a static trace replay viewer.

## Layout

- `atc-benchmark/`: Python package with scenarios, simulator, agents, runners, and tests.
- `viewer/`: static browser viewer for replaying trace JSONL and optional score JSON.
- `scripts/check.py`: local quality gate for validation, tests, lint, type checks, and a batch smoke run.

## Setup

```bash
cd atc-benchmark
pip install -e ".[dev]"
```

Runtime dependencies are intentionally empty. The `dev` extra installs only development tools.

## Run A Scenario

```bash
atc-run scenarios/crossing_conflict_001.json --agent heuristic
```

Outputs default to `outputs/traces/trace.jsonl` and `outputs/scores/score.json`.

## Validate Scenarios

```bash
atc-validate scenarios
atc-validate scenarios/crossing_conflict_001.json
```

Validation checks scenario shape, runway IDs, aircraft fields, events, numeric ranges, and departure queue references before simulation.

## Batch Evaluation

Single-agent mode is backward-compatible:

```bash
atc-batch --agent heuristic
```

Multi-agent comparison writes separated trace/score directories and a combined summary:

```bash
atc-batch --agents heuristic,noop,random
```

## Replay Viewer

Open `viewer/index.html` in a browser, load a trace JSONL file, and optionally load the matching score JSON. The viewer supports scrubbing, playback, conflict links, stable radar bounds, and manifest display.

## Live Mode

Install the live server extra:

```bash
cd atc-benchmark
pip install -e ".[dev,live]"
```

Start the live game server (the scenario argument is optional — without it the server starts in the lobby and the viewer offers a level-select screen):

```bash
atc-live --host 127.0.0.1 --port 8080
# or jump straight into one level:
atc-live scenarios/crossing_conflict_001.json --host 127.0.0.1 --port 8080
```

Run the viewer with `npm run dev`, switch to `Live mode`, keep the endpoint as `ws://localhost:8080/live`, and click `Start`. Pick a level from the lobby; the viewer streams simulator ticks and sends commands back over the same websocket.

Live sessions use the same simulation semantics as batch runs: commands are acknowledged immediately but execute at the next tick (plus any scenario-configured pilot readback delay), emergencies and restricted zones progress in real time, and every tick carries a `running_score`. A level ends when all traffic is handled, an aircraft is lost (immediate game over), the player ends the session (forfeits, `abandoned`), or the tick budget runs out. The server then publishes `level_complete` with the final score, a mission debrief, and a 0-3 star rating, and writes per-session `trace.jsonl` and `score.json` artifacts under `--output-dir` (default `outputs/live`). The viewer shows the debrief with play-again / watch-replay / choose-level actions, tracks best scores per level locally, and can reload the last finished game from the replay screen.

## Local Checks

```bash
python scripts/check.py
```

This runs scenario validation, pytest, ruff, mypy, and a small multi-agent batch smoke run.

## Git On WSL Paths

If Git reports dubious ownership for this repo, add the workspace as a safe directory:

```bash
git config --global --add safe.directory /home/jauwaad/projects/atc-sim
```
