from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "atc-benchmark"


def run_step(name: str, command: list[str], *, cwd: Path = PACKAGE) -> None:
    print(f"==> {name}")
    subprocess.run(command, cwd=cwd, check=True)


def main() -> None:
    python = sys.executable
    run_step("validate scenarios", [python, "-m", "atc_benchmark.runner.validate_scenarios", "scenarios"])
    run_step("pytest", [python, "-m", "pytest"])
    run_step("ruff", [python, "-m", "ruff", "check", "."])
    run_step("mypy", [python, "-m", "mypy", "src"])
    run_step(
        "batch smoke",
        [
            python,
            "-m",
            "atc_benchmark.runner.batch_evaluate",
            "--agents",
            "heuristic,noop,random",
            "--max-ticks",
            "2",
            "--output-dir",
            "outputs/check-smoke",
        ],
    )


if __name__ == "__main__":
    main()
