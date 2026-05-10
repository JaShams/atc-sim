from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    """Return the atc-benchmark project root directory."""
    return Path(__file__).resolve().parents[2]


def scenarios_dir() -> Path:
    """Return the canonical scenarios directory independent of current working directory."""
    return package_root() / "scenarios"


def resolve_scenario_path(path: Path | str) -> Path:
    """Resolve a scenario file path independent of process cwd.

    Absolute paths are returned as-is. Relative paths under ``scenarios/`` are
    anchored to the package scenario directory. Other relative paths are first
    tried against cwd, then against the package root.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    if candidate.parts and candidate.parts[0] == "scenarios":
        return scenarios_dir() / Path(*candidate.parts[1:])

    cwd_candidate = candidate.resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    return package_root() / candidate
