"""Phase 1 regression tests: shared SimulationStepper semantics for batch and live.

Covers: emergency timers progressing under live-style stepping (B8), the
corrected completion condition (B3), live command recording, and running
score snapshots (F2).
"""


from atc_benchmark.simulator.engine import SimulationStepper, run
from atc_benchmark.simulator.models import Aircraft, AirportState, RulesConfig, ScoringConfig, Weather, WorldState


class NoOpAgent:
    def act(self, _obs):
        return {"actions": []}


def _world(aircraft: dict[str, Aircraft], **kwargs) -> WorldState:
    return WorldState(
        time_sec=0,
        tick_sec=5,
        airport=kwargs.pop("airport", AirportState(runway_id="09", active_runway="09")),
        weather=Weather(),
        rules=RulesConfig(),
        scoring=ScoringConfig(),
        aircraft=aircraft,
        **kwargs,
    )


def _arrival(callsign: str, **overrides) -> Aircraft:
    defaults = dict(role="arrival", x_nm=10.0, y_nm=10.0, altitude_ft=5000, speed_kt=200, heading_deg=90, status="airborne")
    defaults.update(overrides)
    return Aircraft(callsign=callsign, **defaults)


def _step(stepper: SimulationStepper) -> dict:
    stepper.begin_tick()
    return stepper.finish_tick()


# --- B8: emergency state progresses under live-style stepping ---

def test_low_fuel_endurance_ticks_down_when_stepping():
    world = _world({"A1": _arrival("A1")})
    world.events = [
        {"type": "low_fuel_emergency", "aircraft": "A1", "time_sec": 0, "remaining_endurance_sec": 10},
    ]
    stepper = SimulationStepper(world)
    _step(stepper)
    assert world.aircraft["A1"].emergency_remaining_endurance_sec == 5
    _step(stepper)
    assert world.aircraft["A1"].emergency_remaining_endurance_sec == 0
    assert world.aircraft["A1"].status == "terminal_failure"


def test_restricted_zone_violations_tracked_when_stepping():
    world = _world({"A1": _arrival("A1", x_nm=-2.0, y_nm=10.0, heading_deg=90, speed_kt=300)})
    world.rules.restricted_zones = [
        {"id": "Z1", "vertices": [{"x_nm": -1.0, "y_nm": 5.0}, {"x_nm": 1.0, "y_nm": 5.0}, {"x_nm": 1.0, "y_nm": 15.0}, {"x_nm": -1.0, "y_nm": 15.0}]},
    ]
    stepper = SimulationStepper(world)
    for _ in range(5):
        _step(stepper)
    assert stepper.restricted_zone_violation_count == 1
    assert stepper.build_score()["score_breakdown"]["restricted_zone_violation"] < 0


# --- B3: completion when all traffic reaches a terminal state ---

def test_is_complete_requires_all_terminal_statuses():
    world = _world({
        "A1": _arrival("A1", status="landed"),
        "A2": _arrival("A2", status="exited_airspace"),
        "A3": _arrival("A3", status="terminal_failure"),
    })
    assert SimulationStepper(world).is_complete()

    world2 = _world({"A1": _arrival("A1", status="landed"), "A2": _arrival("A2")})
    assert not SimulationStepper(world2).is_complete()


def test_run_terminates_early_when_all_traffic_handled(tmp_path):
    # One cleared arrival short final: lands on the first advance; the run
    # must stop shortly after instead of burning all max_ticks.
    ac = _arrival("A1", x_nm=0.0, y_nm=0.5, altitude_ft=200, status="on_final", clearance="cleared_to_land", heading_deg=180)
    world = _world({"A1": ac})
    result = run(world, NoOpAgent(), max_ticks=200, trace_path=tmp_path / "trace.jsonl")
    trace_lines = (tmp_path / "trace.jsonl").read_text().splitlines()
    assert world.aircraft["A1"].status == "landed"
    assert len(trace_lines) < 5
    assert result["efficiency"]["successful_landings"] == 1


# --- Live command recording and running score (F2) ---

def test_commands_between_ticks_are_recorded_in_next_tick():
    world = _world({"A1": _arrival("A1")})
    stepper = SimulationStepper(world)
    _step(stepper)
    # Command arrives between ticks (live mode): recorded in the next record.
    stepper.submit_command({"aircraft": "A1", "type": "assign_heading", "heading": 45})
    record = _step(stepper)
    assert record["actions"] == [{"aircraft": "A1", "type": "assign_heading", "heading": 45}]
    assert world.aircraft["A1"].heading_deg == 45


def test_running_score_reflects_landing():
    ac = _arrival("A1", x_nm=0.0, y_nm=0.5, altitude_ft=200, status="on_final", clearance="cleared_to_land", heading_deg=180)
    world = _world({"A1": ac})
    stepper = SimulationStepper(world)
    before = stepper.build_score()
    assert before["efficiency"]["successful_landings"] == 0
    for _ in range(3):
        _step(stepper)
        if stepper.is_complete():
            break
    after = stepper.build_score()
    assert after["efficiency"]["successful_landings"] == 1
    assert after["score_breakdown"]["successful_landing"] > 0


def test_write_trace_is_repeatable_and_keeps_records(tmp_path):
    world = _world({"A1": _arrival("A1")})
    stepper = SimulationStepper(world)
    _step(stepper)
    _step(stepper)
    stepper.finalize()
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    stepper.write_trace(first)
    stepper.write_trace(second)
    assert first.read_text() == second.read_text()
    assert len(first.read_text().splitlines()) == 2
