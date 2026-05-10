# ATC Trace Replay Viewer (v1)

Minimal static browser viewer for existing benchmark outputs.

## Usage

1. Generate outputs with `atc-run` to produce:
   - trace JSONL (default `outputs/traces/trace.jsonl`)
   - score JSON (default `outputs/score.json`)
2. Open `viewer/index.html` in a browser.
3. Load both files using the file pickers.
4. Scrub ticks with the timeline slider.

## Scope

- Read-only replay and score inspection.
- No simulation controls or backend.
- Deterministic rendering from trace/score files only.
