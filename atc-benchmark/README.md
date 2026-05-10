# ATC Benchmark v1 (Tower + Approach Lite)

## Minimal v1 design

1. **Deterministic simulator core** with fixed tick updates and JSON scenarios.
2. **Decision-point driven agent loop** (agent only called when deterministic monitors trigger).
3. **Structured action schema + validator** before state mutation.
4. **Built-in heuristic baseline agent** for end-to-end benchmark runs.
5. **Outputs**: score JSON + trace JSONL for replay/debugging.

## File structure (v1)

- `scenarios/`: hand-authored deterministic scenario JSONs.
- `src/atc_benchmark/simulator/`: world models, engine, conflict detection, decision points, validation.
- `src/atc_benchmark/agents/`: model-agnostic controller interface + baseline heuristic.
- `src/atc_benchmark/runner/`: CLI entrypoint.
- `tests/`: core rule and harness tests.

## Run

```bash
pip install -e .
atc-run scenarios/crossing_conflict_001.json
pytest
```

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
