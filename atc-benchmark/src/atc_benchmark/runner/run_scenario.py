from __future__ import annotations

import argparse
import json
from pathlib import Path

from atc_benchmark import __version__
from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.agents.noop_agent import NoOpAgent
from atc_benchmark.agents.random_valid_action_agent import RandomValidActionAgent
from atc_benchmark.simulator.engine import load_world, run, scenario_hash


def build_agent(name: str):
    if name == "heuristic":
        return HeuristicAgent()
    if name == "noop":
        return NoOpAgent()
    if name == "random":
        return RandomValidActionAgent(seed=0)
    raise ValueError(f"unknown agent: {name}")


def build_manifest(scenario: Path, world, agent_name: str, max_ticks: int) -> dict:
    scenario_doc = json.loads(scenario.read_text())
    return {
        "scenario_file": scenario.name,
        "scenario_hash": scenario_hash(scenario),
        "agent_name": agent_name,
        "max_ticks": max_ticks,
        "tick_sec": world.tick_sec,
        "rules_config": world.rules.__dict__.copy(),
        "scoring_config": world.scoring.__dict__.copy(),
        "package": {"name": "atc-benchmark", "version": __version__},
        "scenario_metadata": scenario_doc.get("scenario_metadata", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--agent", choices=["heuristic", "noop", "random"], default="heuristic")
    parser.add_argument("--trace", default="outputs/traces/trace.jsonl")
    parser.add_argument("--score", default="outputs/scores/score.json")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    scenario = Path(args.scenario)
    world = load_world(scenario)
    manifest = build_manifest(scenario, world, args.agent, args.max_ticks)
    result = run(world, build_agent(args.agent), args.max_ticks, Path(args.trace), manifest=manifest)
    score_path = Path(args.score)
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
