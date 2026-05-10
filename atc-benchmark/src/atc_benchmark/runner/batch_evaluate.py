from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from atc_benchmark.paths import resolve_scenario_path, scenarios_dir
from atc_benchmark.runner.run_scenario import build_agent, build_manifest
from atc_benchmark.simulator.engine import load_world, run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios-dir", default="scenarios")
    parser.add_argument("--agent", choices=["heuristic", "noop", "random", "llm"], default="heuristic")
    parser.add_argument("--output-dir", default="outputs/batch")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    scenarios_root = resolve_scenario_path(Path(args.scenarios_dir)) if args.scenarios_dir != "scenarios" else scenarios_dir()
    scenarios = sorted(scenarios_root.glob("*.json"))
    out = Path(args.output_dir)
    score_dir = out / "scores"
    trace_dir = out / "traces"
    score_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    tag_counter: Counter[str] = Counter()
    tier_counter: Counter[str] = Counter()
    for scenario in scenarios:
        world = load_world(scenario)
        scenario_doc = json.loads(scenario.read_text())
        metadata = scenario_doc.get("scenario_metadata", {})
        tags = metadata.get("tags", [])
        tier = metadata.get("difficulty_tier")
        tag_counter.update(tags)
        if tier:
            tier_counter[tier] += 1
        manifest = build_manifest(scenario, world, args.agent, args.max_ticks)
        result = run(world, build_agent(args.agent), args.max_ticks, trace_dir / f"{scenario.stem}.jsonl", manifest=manifest)
        (score_dir / f"{scenario.stem}.json").write_text(json.dumps(result, indent=2))
        rows.append(
            {
                "scenario": scenario.name,
                "score": result["score"],
                "invalid_commands": result["control_quality"]["invalid_commands"],
                "active_conflicts_count_total": result["metrics"]["active_conflicts_count_total"],
                "predicted_conflicts_count_total": result["metrics"]["predicted_conflicts_count_total"],
                "runway_unsafe_clearance_count": result["metrics"]["runway_unsafe_clearance_count"],
                "malformed_agent_outputs_count": result["metrics"]["malformed_agent_outputs_count"],
                "throughput_ops_per_hour": result["metrics"]["throughput_ops_per_hour"],
                "tags": tags,
                "difficulty_tier": tier,
            }
        )

    summary = {
        "agent": args.agent,
        "count": len(rows),
        "average_score": (sum(r["score"] for r in rows) / len(rows)) if rows else 0,
        "total_invalid_commands": sum(r["invalid_commands"] for r in rows),
        "total_active_conflicts_count": sum(r["active_conflicts_count_total"] for r in rows),
        "total_predicted_conflicts_count": sum(r["predicted_conflicts_count_total"] for r in rows),
        "total_runway_unsafe_clearances": sum(r["runway_unsafe_clearance_count"] for r in rows),
        "total_malformed_agent_outputs": sum(r["malformed_agent_outputs_count"] for r in rows),
        "average_throughput_ops_per_hour": (sum(r["throughput_ops_per_hour"] for r in rows) / len(rows)) if rows else 0,
        "coverage": {"by_tag": dict(sorted(tag_counter.items())), "by_difficulty_tier": dict(sorted(tier_counter.items()))},
        "scenarios": rows,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario",
                "score",
                "invalid_commands",
                "active_conflicts_count_total",
                "predicted_conflicts_count_total",
                "runway_unsafe_clearance_count",
                "malformed_agent_outputs_count",
                "throughput_ops_per_hour",
                "tags",
                "difficulty_tier",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
