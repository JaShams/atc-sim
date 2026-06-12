# ATC Benchmark v1 (Tower + Approach Lite)

## Minimal v1 design

1. **Deterministic simulator core** with fixed tick updates and JSON scenarios.
2. **Decision-point driven agent loop** (agent only called when deterministic monitors trigger).
3. **Structured action schema + validator** before state mutation.
4. **Built-in heuristic baseline agent** for end-to-end benchmark runs.
5. **Outputs**: score JSON + trace JSONL for replay/debugging.

## File structure (v1)

- `scenarios/`: deterministic scenario JSONs (hand-authored, plus `gen_*` files from `atc-seed`).
- `src/atc_benchmark/simulator/`: world models, engine, conflict detection, decision points, validation.
- `src/atc_benchmark/agents/`: model-agnostic controller interface + baseline heuristic.
- `src/atc_benchmark/runner/`: CLI entrypoint.
- `tests/`: core rule and harness tests.

## Run

```bash
pip install -e ".[dev]"
atc-validate scenarios
atc-run scenarios/crossing_conflict_001.json
atc-batch --agents heuristic,noop,random
pytest
```

## Seeding scenarios (levels)

`atc-seed` procedurally generates scenario JSONs with complete `scenario_metadata`
(description, tags, difficulty tier, stressors, star thresholds, expected baseline
ranges, and generator provenance). Generation is deterministic per seed.

```bash
# Curated multi-template level pack (10 levels across tiers)
atc-seed --pack starter --seed 42

# Variants of one template at one tier
atc-seed --template emergency_inbound --tier expert --count 3 --seed 7

# Validate without writing
atc-seed --pack starter --seed 42 --dry-run
```

Templates: `arrival_rush`, `crossing_conflict`, `departure_pressure`,
`emergency_inbound`, `wind_shift`. Tiers: `tutorial`, `intro`, `intermediate`,
`advanced`, `expert`. Files are written to `scenarios/` by default (override with
`--out`), named `gen_<template>_<tier>_NNN.json`, and validated with
`validate_scenario_document` before writing.

## Live Mode

```bash
pip install -e ".[dev,live]"
atc-live scenarios/crossing_conflict_001.json --host 127.0.0.1 --port 8080
```

Then open `../viewer/index.html`, switch to `Live mode`, and connect to `ws://localhost:8080/live`.

## Action schema (required fields)

Each action must include:

- `type`: one of the allowed action types below.
- `aircraft`: callsign present in the current `world.aircraft` map.

Additional required fields by `type`:

- `assign_heading`: `heading` (integer/float in `[0, 359]`).
- `assign_altitude`: `altitude_ft` (number, must satisfy rules minimum).
- `assign_speed`: `speed_kt` (number, must satisfy rules min/max).
- `clear_to_land`: no extra fields.
- `clear_for_takeoff`: no extra fields.
- `go_around`: no extra fields.
- `hold_short`: no extra fields.
- `hold_position`: no extra fields.
- `no_op`: no extra fields.

Validation notes:

- Unknown action types are rejected with `invalid_action_type`.
- Invalid/missing required numeric fields are rejected by type-specific validators (for example `invalid_heading`, `invalid_altitude`, `invalid_speed`).

## Conflict-quality scoring semantics

Conflict-quality accounting now tracks each `conflict_pair_id` across the full run (multi-tick), not only immediate pre/post-action snapshots. Lifecycle events are:

- `introduced`: pair first appears in predictions.
- `delayed`: an action moves the predicted conflict farther into the future.
- `worsened`: an action moves the predicted conflict sooner.
- `resolved`: previously active pair disappears from predictions.
- `reintroduced`: a previously resolved pair appears again later in the run.

`score_breakdown` fields are backward-compatible by name, but now aggregate from lifecycle transitions over the entire run:

- `conflict_resolved`: reward from total `resolved` events.
- `secondary_conflicts_created`: penalty from `reintroduced` events (newly introduced baseline conflicts are not penalized).
- `conflicts_delayed` and `conflicts_worsened`: reward/penalty from total transition counts.

Exact transition semantics (per `conflict_pair_id`):

- Baseline predictions captured before agent actions can only create `introduced`/`resolved` state, never `delayed`/`worsened`.
- `delayed` and `worsened` are evaluated only during action-phase predictions (`predict_conflicts` immediately after valid actions):
  - `delayed`: `predicted_time_sec` increased vs the pair's previous active predicted time.
  - `worsened`: `predicted_time_sec` decreased vs the pair's previous active predicted time.
- `resolved` occurs whenever an active pair is absent in a later prediction snapshot.
- `reintroduced` occurs when a previously resolved pair appears again later.
- Secondary conflict penalty is intentionally tied only to `reintroduced` transitions. Newly `introduced` conflicts are tracked as lifecycle metrics but are not penalized as secondary creations.

## Live backend transport (`ws://.../live`)

`atc_benchmark.server.live_transport` provides a lightweight ASGI websocket endpoint at `/live` for streaming per-tick simulator snapshots to the viewer.

### Connection lifecycle

1. Client opens a websocket connection to `ws://<host>/live` (or `wss://.../live`).
2. Server accepts the socket.
3. Client sends:

```json
{"type": "subscribe_tick_stream"}
```

4. Server registers the connection as a tick-stream subscriber.
5. On each simulation tick, backend publishes a tick envelope to all subscribers.
6. On disconnect, server removes the subscriber queue.

### Message schema

The viewer (`viewer/src/useViewerState.js` `handleLiveEnvelope`) accepts either:

- an envelope with `tick.state`, or
- a raw tick object with `state`.

This transport emits envelopes with the shape:

```json
{
  "type": "tick",
  "tick": {
    "tick_id": 12,
    "time": 60,
    "state": {
      "time_sec": 60,
      "airport": {},
      "weather": {},
      "aircraft": {}
    }
  }
}
```

Required fields for viewer compatibility:

- top-level `type` = `"tick"`
- `tick.state` object present for every published simulation tick

Optional tick metadata may be included alongside `tick_id`, `time`, and `state`.
