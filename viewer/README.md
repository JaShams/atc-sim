# ATC Viewer

React + Konva client for the ATC benchmark: a live game UI and a trace replay viewer.

## Run

From the repository root:

```bash
npm install
npm run dev
```

Open the printed URL (default `http://127.0.0.1:14173`).

## Modes

### Live mode

Start the game server first (see the root README):

```bash
cd atc-benchmark
atc-live --port 8080
```

Switch the viewer to Live mode and click `Start`. You get a level-select
lobby, flight strips, a running score HUD, a text command line (see the
in-app command reference), and a mission debrief with stars when the level
ends. Best scores are stored locally per level.

### Replay mode

Load a trace JSONL (from `atc-run`, `atc-batch`, or a finished live game's
artifacts) plus an optional score JSON, or click `Load last run` to reload
the most recent finished live game. Scrub the timeline, inspect ticks,
aircraft, and score component changes.

## Tests

```bash
npm run test:e2e
```

Playwright drives a real `atc-live` server; the Python venv at `.venv/` must
have the package installed with the `live` extra.
