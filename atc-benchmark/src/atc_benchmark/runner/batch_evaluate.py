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
    parser.add_argument("--agents", help="Comma-separated agents to compare, e.g. heuristic,noop,random")
    parser.add_argument("--output-dir", default="outputs/batch")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    agent_names = [name.strip() for name in args.agents.split(",") if name.strip()] if args.agents else [args.agent]
    invalid_agents = sorted(set(agent_names) - {"heuristic", "noop", "random", "llm"})
    if invalid_agents:
        raise SystemExit(f"unknown agents: {', '.join(invalid_agents)}")

    scenarios_root = resolve_scenario_path(Path(args.scenarios_dir)) if args.scenarios_dir != "scenarios" else scenarios_dir()
    scenarios = sorted(scenarios_root.glob("*.json"))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    multi_agent = len(agent_names) > 1

    rows = []
    tag_counter: Counter[str] = Counter()
    tier_counter: Counter[str] = Counter()
    for scenario in scenarios:
        scenario_doc = json.loads(scenario.read_text())
        metadata = scenario_doc.get("scenario_metadata", {})
        tags = metadata.get("tags", [])
        tier = metadata.get("difficulty_tier")
        tag_counter.update(tags)
        if tier:
            tier_counter[tier] += 1
        expected_ranges = metadata.get("expected_baseline_ranges", {})
        for agent_name in agent_names:
            world = load_world(scenario)
            agent_root = out / agent_name if multi_agent else out
            score_dir = agent_root / "scores"
            trace_dir = agent_root / "traces"
            score_dir.mkdir(parents=True, exist_ok=True)
            trace_dir.mkdir(parents=True, exist_ok=True)
            manifest = build_manifest(scenario, world, agent_name, args.max_ticks)
            result = run(world, build_agent(agent_name), args.max_ticks, trace_dir / f"{scenario.stem}.jsonl", manifest=manifest)
            (score_dir / f"{scenario.stem}.json").write_text(json.dumps(result, indent=2))
            row = {
                "scenario": scenario.name,
                "agent": agent_name,
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
            row["expected_baseline_pass"] = _expected_baseline_pass(row, expected_ranges)
            rows.append(row)

    summary = {
        "agent": args.agent if not multi_agent else None,
        "agents": agent_names,
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
                "agent",
                "score",
                "invalid_commands",
                "active_conflicts_count_total",
                "predicted_conflicts_count_total",
                "runway_unsafe_clearance_count",
                "malformed_agent_outputs_count",
                "throughput_ops_per_hour",
                "expected_baseline_pass",
                "tags",
                "difficulty_tier",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _expected_baseline_pass(row: dict, expected_ranges: dict) -> bool | None:
    if not expected_ranges:
        return None
    for metric, bounds in expected_ranges.items():
        if metric not in row or not isinstance(bounds, list) or len(bounds) != 2:
            continue
        lower, upper = bounds
        if row[metric] < lower or row[metric] > upper:
            return False
    return True


if __name__ == "__main__":
    main()
