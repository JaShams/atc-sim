from __future__ import annotations

import argparse
import json
from pathlib import Path

from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.simulator.engine import load_world, run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--trace", default="outputs/traces/trace.jsonl")
    parser.add_argument("--score", default="outputs/scores/score.json")
    parser.add_argument("--max-ticks", type=int, default=300)
    args = parser.parse_args()

    world = load_world(Path(args.scenario))
    result = run(world, HeuristicAgent(), args.max_ticks, Path(args.trace))
    score_path = Path(args.score)
    score_path.parent.mkdir(parents=True, exist_ok=True)
    score_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
