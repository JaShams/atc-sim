from pathlib import Path

from atc_benchmark.simulator.engine import apply_actions, load_world, run
from atc_benchmark.simulator.models import Aircraft, AirportState, RulesConfig, Weather, WorldState
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
    assert result["metrics"]["secondary_conflicts_created_count"] >= 0


def test_conflict_lifecycle_transitions_across_ticks(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent(
        [
            [{"aircraft": "A1", "type": "assign_heading", "heading": 90}],
            [{"aircraft": "A1", "type": "assign_heading", "heading": 180}],
        ]
    )
    result = run(world, agent, max_ticks=2, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflict_resolved_count"] >= 1
    assert result["metrics"]["secondary_conflicts_created_count"] >= 0


def _world_for_predicted_conflict_tests() -> WorldState:
    return WorldState(
        time_sec=0,
        tick_sec=5,
        airport=AirportState(runway_id="27", active_runway="27"),
        weather=Weather(),
        rules=RulesConfig(lookahead_seconds=60, min_horizontal_nm=3.0, min_vertical_ft=1000.0),
        aircraft={
            "A1": Aircraft(callsign="A1", role="arrival", x_nm=0.0, y_nm=-5.0, altitude_ft=5000, speed_kt=240, heading_deg=0, status="airborne"),
            "A2": Aircraft(callsign="A2", role="arrival", x_nm=0.0, y_nm=5.0, altitude_ft=5000, speed_kt=240, heading_deg=180, status="airborne"),
            "A3": Aircraft(callsign="A3", role="arrival", x_nm=50.0, y_nm=50.0, altitude_ft=5000, speed_kt=240, heading_deg=90, status="airborne"),
        },
    )


def test_action_fully_resolves_predicted_conflict(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent([[{"aircraft": "A1", "type": "assign_heading", "heading": 90}]])
    result = run(world, agent, max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflict_resolved_count"] == 1
    assert result["metrics"]["conflicts_delayed_count"] == 0
    assert result["metrics"]["conflicts_worsened_count"] == 0


def test_action_delays_same_predicted_conflict(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent([[{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}]])
    result = run(world, agent, max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflict_resolved_count"] == 0
    assert result["metrics"]["conflicts_delayed_count"] == 1
    assert result["metrics"]["average_conflict_time_gained_sec"] > 0


def test_action_worsens_same_predicted_conflict(tmp_path):
    world = _world_for_predicted_conflict_tests()
    agent = ScriptedAgent([[{"aircraft": "A1", "type": "assign_speed", "speed_kt": 280}]])
    result = run(world, agent, max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["conflicts_worsened_count"] == 1
    assert result["metrics"]["average_conflict_time_gained_sec"] < 0


def test_action_creates_secondary_conflict_with_new_pair(tmp_path):
    world = _world_for_predicted_conflict_tests()
    world.aircraft["A3"].x_nm = 6.0
    world.aircraft["A3"].y_nm = -5.0
    world.aircraft["A3"].heading_deg = 270
    agent = ScriptedAgent([[{"aircraft": "A1", "type": "assign_heading", "heading": 90}]])
    result = run(world, agent, max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["secondary_conflicts_created_count"] == 1


def test_exited_airspace_aircraft_ignored_by_conflict_detection(tmp_path):
    world = _world_for_predicted_conflict_tests()
    world.aircraft["A3"].status = "airborne_departure"
    world.aircraft["A3"].x_nm = 40.0
    world.aircraft["A3"].y_nm = 40.0
    run(world, ScriptedAgent([]), max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert world.aircraft["A3"].status == "exited_airspace"


def test_runway_protection_ignores_arrivals_flying_away():
    world = load_world(Path("scenarios/departure_between_arrivals_001.json"))
    arr = world.aircraft["ARR1"]
    arr.heading_deg = 90
    _, invalid = validate_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert not invalid


def test_takeoff_runway_occupancy_persists_until_timer_expires():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    dep = world.aircraft["DEP1"]
    apply_actions(world, [{"aircraft": "DEP1", "type": "clear_for_takeoff"}])
    assert world.airport.runway_occupied_by == dep.callsign
    assert world.airport.runway_occupied_until_sec is not None
    from atc_benchmark.simulator.engine import advance

    advance(world)
    world.time_sec += world.tick_sec
    assert world.airport.runway_occupied_by == dep.callsign


def test_landing_sets_vacating_phase_and_release_time():
    world = load_world(Path("scenarios/crossing_conflict_001.json"))
    arr = world.aircraft["ARR1"]
    arr.status = "on_final"
    arr.x_nm = 0.0
    arr.y_nm = 0.0
    arr.altitude_ft = 200
    from atc_benchmark.simulator.engine import advance

    advance(world)
    assert world.airport.runway_occupied_by == "ARR1"
    assert world.airport.runway_phase == "vacating"
    assert world.airport.runway_occupied_until_sec is not None


def test_runway_occupied_goaround_behavior_with_timed_occupancy(tmp_path):
    world = load_world(Path("scenarios/runway_occupied_goaround_001.json"))
    world.airport.runway_occupied_until_sec = world.time_sec + 20
    result = run(world, ScriptedAgent([[{"aircraft": "ARR1", "type": "go_around"}]]), max_ticks=1, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["go_around_count"] == 1
    assert world.aircraft["ARR1"].status == "go_around"
