from __future__ import annotations

import argparse
import json
from pathlib import Path

from atc_benchmark.paths import resolve_scenario_path, scenarios_dir
from atc_benchmark.simulator.scenario_validation import ScenarioValidationError, validate_scenario_document


def _scenario_files(path: Path) -> list[Path]:
    resolved = resolve_scenario_path(path)
    if resolved.is_dir():
        return sorted(resolved.glob("*.json"))
    return [resolved]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=str(scenarios_dir()), help="Scenario JSON file or directory")
    args = parser.parse_args()

    failed = False
    for scenario in _scenario_files(Path(args.path)):
        try:
            validate_scenario_document(json.loads(scenario.read_text()), scenario)
        except (OSError, json.JSONDecodeError, ScenarioValidationError) as exc:
            failed = True
            print(exc)
        else:
            print(f"valid: {scenario}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
