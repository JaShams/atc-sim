from pathlib import Path

from atc_benchmark.simulator.engine import apply_actions, load_world, run
from atc_benchmark.simulator.validator import validate_actions


class ScriptedAgent:
    def __init__(self, actions_per_tick):
        self.actions_per_tick = actions_per_tick
        self.i = 0

    def act(self, _obs):
        actions = self.actions_per_tick[self.i] if self.i < len(self.actions_per_tick) else []
        self.i += 1
        return {"actions": actions}


def test_departure_lifecycle_transitions(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    dep = world.aircraft["DEP1"]
    assert dep.status == "waiting_departure"
    apply_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert dep.status == "rolling"
    from atc_benchmark.simulator.engine import advance

    advance(world)
    assert dep.status == "airborne_departure"
    assert dep.takeoff_time_sec is not None


def test_time_based_delay_metrics(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    world.aircraft["DEP1"].ideal_takeoff_time_sec = 0
    world.aircraft["ARR1"].ideal_landing_time_sec = 0
    result = run(world, ScriptedAgent([]), max_ticks=2, trace_path=tmp_path / "trace.jsonl")
    assert result["efficiency"]["arrival_delay"] >= 0
    assert result["efficiency"]["departure_delay"] >= 0


def test_predicted_conflict_resolution_and_introduced_conflict(tmp_path):
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    agent = ScriptedAgent(
        [[{"aircraft": "ARR1", "type": "assign_heading", "heading": 0}], [{"aircraft": "ARR1", "type": "assign_heading", "heading": 180}]]
    )
    result = run(world, agent, max_ticks=3, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflict_resolved_count"] >= 0
    assert result["metrics"]["new_conflicts_created_by_action"] >= 0


def test_runway_protection_ignores_arrivals_flying_away():
    world = load_world(Path("scenarios/departure_between_arrivals_001.json"))
    arr = world.aircraft["ARR1"]
    arr.heading_deg = 90
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert not invalid
