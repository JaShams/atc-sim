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
