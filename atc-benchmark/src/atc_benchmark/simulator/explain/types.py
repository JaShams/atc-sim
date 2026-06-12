from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CallReasonType(str, Enum):
    DECISION_POINT = "decision_point"
    EVENT = "event"
    NONE = "none"


class OutcomeKind(str, Enum):
    HELPED = "helped"
    HURT = "hurt"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ScoreComponentId:
    BASE_SCORE = "base_score"
    LOSS_OF_SEPARATION = "loss_of_separation"
    INVALID_COMMAND = "invalid_command"
    SECONDARY_CONFLICTS_CREATED = "secondary_conflicts_created"
    CONFLICTS_WORSENED = "conflicts_worsened"
    CONFLICTS_DELAYED = "conflicts_delayed"
    CONFLICT_RESOLVED = "conflict_resolved"
    ARRIVAL_DELAY_SEC = "arrival_delay_sec"
    DEPARTURE_DELAY_SEC = "departure_delay_sec"
    SUCCESSFUL_LANDING = "successful_landing"
    SUCCESSFUL_DEPARTURE = "successful_departure"
    EMERGENCY_HANDLED = "emergency_handled"
    EMERGENCY_UNHANDLED = "emergency_unhandled"
    EMERGENCY_PRIORITY_COMPLIANCE = "emergency_priority_compliance"
    RESTRICTED_ZONE_VIOLATION = "restricted_zone_violation"
    RUNWAY_INCURSION = "runway_incursion"
    HANDOFF_COMPLETED = "handoff_completed"
    MISSED_HANDOFF = "missed_handoff"


@dataclass
class CallReason:
    type: str = CallReasonType.NONE.value
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RankedAlternative:
    rank: int
    action: dict[str, Any]
    score: float | None = None


@dataclass
class TickOutcome:
    kind: str = OutcomeKind.UNKNOWN.value
    metric: str = ""
    value: float | int | str | None = None
    immediate_delta: float | None = None
    window_delta: float | None = None
    normalization_value: float | None = None
    epsilon_immediate: float | None = None
    epsilon_window: float | None = None
    horizon_ticks: int | None = None


@dataclass
class TickExplanation:
    tick_id: int
    sim_time: int
    call_reason: CallReason = field(default_factory=CallReason)
    trigger_context: dict[str, Any] = field(default_factory=dict)
    action_chosen: list[dict[str, Any]] = field(default_factory=list)
    alternatives_considered: list[RankedAlternative] = field(default_factory=list)
    outcome: TickOutcome = field(default_factory=TickOutcome)
    score_before: float | None = None
    score_after: float | None = None
    score_delta_by_component: dict[str, float] = field(default_factory=dict)



def tick_explanation_to_dict(explanation: TickExplanation) -> dict[str, Any]:
    return asdict(explanation)
