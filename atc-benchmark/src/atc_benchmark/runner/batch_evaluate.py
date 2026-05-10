from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from atc_benchmark.runner.run_scenario import build_agent, build_manifest
from atc_benchmark.simulator.engine import load_world, run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios-dir", default="scenarios")
    parser.add_argument("--agent", choices=["heuristic", "noop", "random"], default="heuristic")
    parser.add_argument("--output-dir", default="outputs/batch")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    scenarios = sorted(Path(args.scenarios_dir).glob("*.json"))
    out = Path(args.output_dir)
    score_dir = out / "scores"
    trace_dir = out / "traces"
    score_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for scenario in scenarios:
        world = load_world(scenario)
        manifest = build_manifest(scenario, world, args.agent, args.max_ticks)
        result = run(world, build_agent(args.agent), args.max_ticks, trace_dir / f"{scenario.stem}.jsonl", manifest=manifest)
        (score_dir / f"{scenario.stem}.json").write_text(json.dumps(result, indent=2))
        rows.append({"scenario": scenario.name, "score": result["score"], "invalid_commands": result["control_quality"]["invalid_commands"]})

    summary = {
        "agent": args.agent,
        "count": len(rows),
        "average_score": (sum(r["score"] for r in rows) / len(rows)) if rows else 0,
        "scenarios": rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["scenario", "score", "invalid_commands"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
