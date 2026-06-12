from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atc_benchmark.simulator.engine import run


@dataclass(slots=True)
class MissionDebrief:
    outcome: str
    headline: str
    details: list[str]

    def format(self) -> str:
        return "\n".join([self.headline, *[f"- {line}" for line in self.details]])

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "headline": self.headline, "details": list(self.details)}


DEFAULT_STAR_THRESHOLDS = [1.0, 60.0, 85.0]


def star_rating(score_value: float, metadata: dict | None = None) -> int:
    """Map a final score to a 0-3 star rating.

    Scenarios can override the cutoffs with ``scenario_metadata.star_thresholds``
    (ascending list of minimum scores for 1, 2, and 3 stars).
    """
    thresholds = (metadata or {}).get("star_thresholds") or DEFAULT_STAR_THRESHOLDS
    stars = 0
    for rank, minimum in enumerate(thresholds[:3], start=1):
        if score_value >= minimum:
            stars = rank
    return stars


def build_mission_debrief(score: dict, *, outcome_override: str | None = None) -> MissionDebrief:
    metrics = score.get("metrics", {})
    safety = score.get("safety", {})
    efficiency = score.get("efficiency", {})
    metadata = score.get("run_manifest", {}).get("scenario_metadata", {})
    stressors = metadata.get("intended_stressors") or []
    tags = metadata.get("tags") or []

    loss_sep = int(safety.get("loss_of_separation") or 0)
    emergency_unhandled = int(metrics.get("emergency_unhandled_count") or 0)
    invalid = int(score.get("control_quality", {}).get("invalid_commands") or 0)
    successful_ops = int(efficiency.get("successful_landings") or 0) + int(efficiency.get("successful_departures") or 0)

    timeout = bool(metrics.get("emergency_unhandled_count") and successful_ops == 0)
    if outcome_override is not None:
        outcome = outcome_override
    elif loss_sep == 0 and emergency_unhandled == 0:
        outcome = "success"
    elif timeout:
        outcome = "timeout"
    else:
        outcome = "failure"

    headline = f"=== Mission debrief: {outcome.upper()} | score={score.get('score', 0):.1f} ==="
    details = [
        f"Ops completed: {successful_ops} (landings={efficiency.get('successful_landings', 0)}, departures={efficiency.get('successful_departures', 0)})",
        f"Safety: loss_of_separation={loss_sep}, restricted_zone_violations={metrics.get('restricted_zone_violation_count', 0)}",
        f"Control quality: invalid_commands={invalid}, malformed_outputs={metrics.get('malformed_agent_outputs_count', 0)}",
    ]
    if stressors:
        details.append(f"Stressors exercised: {', '.join(stressors)}")
    if tags:
        details.append(f"Scenario tags: {', '.join(tags)}")
    return MissionDebrief(outcome=outcome, headline=headline, details=details)


class GameSession:
    def __init__(self, world, agent, *, max_ticks: int, trace_path: Path, manifest: dict | None = None) -> None:
        self.world = world
        self.agent = agent
        self.max_ticks = max_ticks
        self.trace_path = trace_path
        self.manifest = manifest or {}

    def start_briefing(self) -> str:
        metadata = self.manifest.get("scenario_metadata", {}) if isinstance(self.manifest, dict) else {}
        tags = metadata.get("tags") or []
        stressors = metadata.get("intended_stressors") or []
        scenario_file = self.manifest.get("scenario_file", "unknown scenario")
        difficulty = metadata.get("difficulty_tier", "unspecified")
        lines = [
            "=== Scenario briefing ===",
            f"Mission: {scenario_file} ({difficulty})",
            f"Tags: {', '.join(tags) if tags else 'none'}",
            f"Stressors: {', '.join(stressors) if stressors else 'none'}",
            f"Tick budget: {self.max_ticks} @ {self.world.tick_sec}s",
        ]
        return "\n".join(lines)

    def run(self) -> dict:
        return run(self.world, self.agent, self.max_ticks, self.trace_path, manifest=self.manifest)

    def mission_debrief(self, score: dict) -> MissionDebrief:
        return build_mission_debrief(score)
