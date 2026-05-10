from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from atc_benchmark import __version__
from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.agents.llm_agent import LLMAgent
from atc_benchmark.agents.noop_agent import NoOpAgent
from atc_benchmark.agents.random_valid_action_agent import RandomValidActionAgent
from atc_benchmark.paths import resolve_scenario_path
from atc_benchmark.simulator.engine import load_world, run, scenario_hash


def build_agent(name: str):
    if name == "heuristic":
        return HeuristicAgent()
    if name == "noop":
        return NoOpAgent()
    if name == "random":
        return RandomValidActionAgent(seed=0)
    if name == "llm":
        return LLMAgent.from_env()
    raise ValueError(f"unknown agent: {name}")


def _git_metadata(scenario: Path) -> dict[str, object]:
    root = scenario.resolve().parents[1]
    metadata: dict[str, object] = {"git_commit": None, "git_dirty": None, "git_metadata_error": None}
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        metadata["git_metadata_error"] = str(exc)
    else:
        metadata["git_commit"] = commit.stdout.strip()
        metadata["git_dirty"] = bool(status.stdout.strip())
    return metadata


def agent_config(agent_name: str) -> dict[str, object]:
    config: dict[str, object] = {"name": agent_name}
    if agent_name == "random":
        config["seed"] = 0
    if agent_name == "llm":
        config["client"] = "stub"
    return config


def build_manifest(scenario: Path, world, agent_name: str, max_ticks: int) -> dict:
    scenario_doc = json.loads(scenario.read_text())
    manifest = {
        "scenario_file": scenario.name,
        "scenario_hash": scenario_hash(scenario),
        "agent_name": agent_name,
        "agent_config": agent_config(agent_name),
        "max_ticks": max_ticks,
        "tick_sec": world.tick_sec,
        "rules_config": world.rules.__dict__.copy(),
        "scoring_config": world.scoring.__dict__.copy(),
        "package": {"name": "atc-benchmark", "version": __version__},
        "runtime": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
        "scenario_metadata": scenario_doc.get("scenario_metadata", {}),
    }
    manifest.update(_git_metadata(scenario))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--agent", choices=["heuristic", "noop", "random", "llm"], default="heuristic")
    parser.add_argument("--trace", default="outputs/traces/trace.jsonl")
    parser.add_argument("--score", default="outputs/scores/score.json")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    scenario = resolve_scenario_path(Path(args.scenario))
    world = load_world(scenario)
    manifest = build_manifest(scenario, world, args.agent, args.max_ticks)
    result = run(world, build_agent(args.agent), args.max_ticks, Path(args.trace), manifest=manifest)
    score_path = Path(args.score)
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
