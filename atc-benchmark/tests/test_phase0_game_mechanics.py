"""Regression tests for the Phase 0 game-mechanics fixes.

Covers: holding-aircraft separation, turn-rate-limited heading completion,
landing clearance + runway incursions, off-tick event firing, go-around
altitude capture, and aircraft-less no_op validation.
"""

from atc_benchmark.simulator.conflict_detection import detect_conflicts, predict_conflicts
from atc_benchmark.simulator.engine import advance, apply_actions, apply_events, run
from atc_benchmark.simulator.models import Aircraft, AirportState, RulesConfig, ScoringConfig, Weather, WorldState
from atc_benchmark.simulator.validator import validate_actions


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


class ScriptedAgent:
    def __init__(self, actions_per_tick):
        self.actions_per_tick = actions_per_tick
        self.i = 0

    def act(self, _obs):
        actions = self.actions_per_tick[self.i] if self.i < len(self.actions_per_tick) else []
        self.i += 1
        return {"actions": actions}


# --- B1: holding aircraft must not be invisible to separation checks ---

def test_holding_aircraft_detected_in_active_conflicts():
    world = _world({
        "A1": _arrival("A1", status="holding"),
        "A2": _arrival("A2", status="holding", x_nm=10.1),
    })
    conflicts = detect_conflicts(world)
    assert len(conflicts) == 1
    assert set(conflicts[0]["aircraft"]) == {"A1", "A2"}


def test_holding_aircraft_included_in_predicted_conflicts():
    world = _world({
        "A1": _arrival("A1", status="holding", x_nm=0.0, y_nm=-4.0, heading_deg=0),
        "A2": _arrival("A2", status="airborne", x_nm=0.0, y_nm=4.0, heading_deg=180),
    })
    predictions = predict_conflicts(world)
    assert predictions
    assert set(predictions[0]["aircraft"]) == {"A1", "A2"}


# --- B2: turn-rate-limited headings must complete over subsequent ticks ---

def test_assign_heading_with_turn_rate_completes_over_ticks():
    ac = _arrival("E1", heading_deg=0, max_turn_rate_deg_per_sec=2.0)
    world = _world({"E1": ac})
    apply_actions(world, [{"aircraft": "E1", "type": "assign_heading", "heading": 180}])
    assert ac.target_heading_deg == 180

    for _ in range(30):
        advance(world)
        world.time_sec += world.tick_sec
        if ac.target_heading_deg is None:
            break
    assert ac.heading_deg == 180
    assert ac.target_heading_deg is None


def test_assign_heading_turn_rate_caps_per_tick_change():
    ac = _arrival("E1", heading_deg=0, max_turn_rate_deg_per_sec=2.0)
    world = _world({"E1": ac})
    apply_actions(world, [{"aircraft": "E1", "type": "assign_heading", "heading": 90}])
    advance(world)
    # 2 deg/sec * 5 sec tick = 10 degrees max per tick.
    assert ac.heading_deg == 10


def test_resume_procedure_clears_pending_manual_heading_target():
    ac = _arrival(
        "E1",
        x_nm=0.0,
        y_nm=0.0,
        heading_deg=0,
        max_turn_rate_deg_per_sec=2.0,
        route_id="R1",
        procedure_type="STAR",
        waypoints=[{"name": "WP1", "x_nm": 10.0, "y_nm": 0.0}],
    )
    world = _world({"E1": ac})

    apply_actions(world, [{"aircraft": "E1", "type": "assign_heading", "heading": 180}])
    advance(world)
    world.time_sec += world.tick_sec
    assert ac.target_heading_deg == 180

    apply_actions(world, [{"aircraft": "E1", "type": "resume_procedure"}])
    assert ac.target_heading_deg is None

    advance(world)
    world.time_sec += world.tick_sec
    # The managed route steers toward WP1 (roughly east); the stale manual
    # target must not drag the heading back toward 180.
    assert 80 <= ac.heading_deg <= 100


def test_assign_heading_without_turn_rate_is_immediate():
    ac = _arrival("A1", heading_deg=0)
    world = _world({"A1": ac})
    apply_actions(world, [{"aircraft": "A1", "type": "assign_heading", "heading": 180}])
    assert ac.heading_deg == 180
    assert ac.target_heading_deg is None


# --- B10: landing requires clearance; landing on an occupied runway is an incursion ---

def test_arrival_without_clearance_does_not_land():
    ac = _arrival("A1", x_nm=0.0, y_nm=0.0, altitude_ft=200, clearance=None)
    world = _world({"A1": ac})
    advance(world)
    assert ac.status != "landed"


def test_cleared_arrival_lands():
    ac = _arrival("A1", x_nm=0.0, y_nm=0.0, altitude_ft=200, status="on_final", clearance="cleared_to_land")
    world = _world({"A1": ac})
    events = advance(world)
    assert ac.status == "landed"
    assert events == []


def test_landing_on_occupied_runway_emits_incursion_event():
    ac = _arrival("A1", x_nm=0.0, y_nm=0.0, altitude_ft=200, status="on_final", clearance="cleared_to_land")
    airport = AirportState(runway_id="09", active_runway="09", runway_occupied_by="DEP9", runway_occupied_until_sec=100)
    world = _world({"A1": ac}, airport=airport)
    events = advance(world)
    incursions = [e for e in events if e["type"] == "runway_incursion"]
    assert len(incursions) == 1
    assert incursions[0]["aircraft"] == "A1"
    assert incursions[0]["occupied_by"] == "DEP9"


def test_runway_incursion_penalized_in_score(tmp_path):
    ac = _arrival("A1", x_nm=0.0, y_nm=0.5, altitude_ft=200, status="on_final", clearance="cleared_to_land", heading_deg=180)
    blocker = _arrival("A2", x_nm=20.0, y_nm=20.0)
    airport = AirportState(runway_id="09", active_runway="09", runway_occupied_by="A2", runway_occupied_until_sec=10_000)
    world = _world({"A1": ac, "A2": blocker}, airport=airport)
    result = run(world, ScriptedAgent([]), max_ticks=3, trace_path=tmp_path / "trace.jsonl")
    assert result["metrics"]["runway_incursion_count"] == 1
    assert result["score_breakdown"]["runway_incursion"] < 0


# --- B4: events scheduled between ticks must still fire ---

def test_event_with_off_tick_time_fires_at_next_tick():
    world = _world({"A1": _arrival("A1")})
    world.events = [{"type": "emergency_declare", "aircraft": "A1", "time_sec": 7}]

    world.time_sec = 5
    assert apply_events(world) == []

    world.time_sec = 10
    triggered = apply_events(world)
    assert len(triggered) == 1
    assert world.aircraft["A1"].emergency is True

    # Must not re-fire once applied.
    world.time_sec = 15
    assert apply_events(world) == []


# --- B12: go-around captures a target altitude and cancels the landing clearance ---

def test_go_around_levels_off_and_cancels_clearance():
    ac = _arrival("A1", x_nm=3.0, y_nm=0.0, altitude_ft=800, status="on_final", clearance="cleared_to_land")
    world = _world({"A1": ac})
    apply_actions(world, [{"aircraft": "A1", "type": "go_around"}])
    assert ac.status == "go_around"
    assert ac.clearance is None
    assert ac.target_altitude_ft == 3000.0
    assert ac.vertical_rate_fpm == 1200

    for _ in range(40):
        advance(world)
        world.time_sec += world.tick_sec
        if ac.vertical_rate_fpm == 0:
            break
    assert ac.altitude_ft == 3000.0
    assert ac.vertical_rate_fpm == 0


def test_go_around_aircraft_can_be_cleared_to_land_again():
    # Aligned with runway 09: on the extended centerline, heading 090, inside 15 nm.
    ac = _arrival("A1", x_nm=-5.0, y_nm=0.0, altitude_ft=2000, status="go_around", heading_deg=90)
    world = _world({"A1": ac})
    valid, invalid = validate_actions(world, [{"aircraft": "A1", "type": "clear_to_land"}])
    assert valid and not invalid


# --- B5: an aircraft-less no_op is a valid command, not a penalty ---

def test_no_op_without_aircraft_is_valid():
    world = _world({"A1": _arrival("A1")})
    valid, invalid = validate_actions(world, [{"type": "no_op"}])
    assert valid == [{"type": "no_op"}]
    assert invalid == []


def test_no_op_without_aircraft_applies_without_error(tmp_path):
    class NoOpOnlyAgent:
        def act(self, _obs):
            return {"actions": [{"type": "no_op"}]}

    world = _world({
        "A1": _arrival("A1", x_nm=0.0, y_nm=-4.0, heading_deg=0),
        "A2": _arrival("A2", x_nm=0.0, y_nm=4.0, heading_deg=180),
    })
    result = run(world, NoOpOnlyAgent(), max_ticks=2, trace_path=tmp_path / "trace.jsonl")
    assert result["control_quality"]["invalid_commands"] == 0
