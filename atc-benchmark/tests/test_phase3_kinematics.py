"""Phase 3 regression tests: opt-in smooth kinematics and takeoff roll.

All features are gated by RulesConfig so existing scenarios keep their
instant-change semantics unless they opt in.
"""

from atc_benchmark.simulator.engine import advance, apply_actions
from atc_benchmark.simulator.models import Aircraft, AirportState, RulesConfig, ScoringConfig, Weather, WorldState


def _world(aircraft: dict[str, Aircraft], rules: RulesConfig | None = None) -> WorldState:
    return WorldState(
        time_sec=0,
        tick_sec=5,
        airport=AirportState(runway_id="09", active_runway="09", departure_queue=[cs for cs, a in aircraft.items() if a.role == "departure"]),
        weather=Weather(),
        rules=rules or RulesConfig(),
        scoring=ScoringConfig(),
        aircraft=aircraft,
    )


def _arrival(callsign: str, **overrides) -> Aircraft:
    defaults = dict(role="arrival", x_nm=10.0, y_nm=10.0, altitude_ft=5000, speed_kt=200, heading_deg=0, status="airborne")
    defaults.update(overrides)
    return Aircraft(callsign=callsign, **defaults)


def _tick(world: WorldState) -> None:
    advance(world)
    world.time_sec += world.tick_sec


# --- default turn rate (smooth turns for all aircraft when enabled) ---

def test_default_turn_rate_applies_to_all_aircraft():
    rules = RulesConfig(default_turn_rate_deg_per_sec=3.0)
    ac = _arrival("A1")
    world = _world({"A1": ac}, rules)
    apply_actions(world, [{"aircraft": "A1", "type": "assign_heading", "heading": 90}])
    assert ac.target_heading_deg == 90
    _tick(world)
    assert ac.heading_deg == 15  # 3 deg/s * 5 s
    for _ in range(10):
        _tick(world)
        if ac.target_heading_deg is None:
            break
    assert ac.heading_deg == 90


def test_aircraft_specific_turn_rate_overrides_default():
    rules = RulesConfig(default_turn_rate_deg_per_sec=3.0)
    ac = _arrival("A1", max_turn_rate_deg_per_sec=1.0)
    world = _world({"A1": ac}, rules)
    apply_actions(world, [{"aircraft": "A1", "type": "assign_heading", "heading": 90}])
    _tick(world)
    assert ac.heading_deg == 5  # 1 deg/s * 5 s, not the 3 deg/s default


def test_instant_turns_remain_default_behavior():
    ac = _arrival("A1")
    world = _world({"A1": ac})
    apply_actions(world, [{"aircraft": "A1", "type": "assign_heading", "heading": 90}])
    assert ac.heading_deg == 90
    assert ac.target_heading_deg is None


# --- speed ramp ---

def test_speed_change_ramps_when_enabled():
    rules = RulesConfig(speed_change_rate_kt_per_sec=2.0)
    ac = _arrival("A1", speed_kt=240)
    world = _world({"A1": ac}, rules)
    apply_actions(world, [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}])
    assert ac.target_speed_kt == 200
    assert ac.speed_kt == 240
    _tick(world)
    assert ac.speed_kt == 230  # 2 kt/s * 5 s
    for _ in range(10):
        _tick(world)
        if ac.target_speed_kt is None:
            break
    assert ac.speed_kt == 200


def test_speed_change_instant_by_default():
    ac = _arrival("A1", speed_kt=240)
    world = _world({"A1": ac})
    apply_actions(world, [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 200}])
    assert ac.speed_kt == 200
    assert ac.target_speed_kt is None


def test_resume_procedure_clears_pending_speed_target():
    rules = RulesConfig(speed_change_rate_kt_per_sec=2.0)
    ac = _arrival(
        "A1",
        speed_kt=240,
        route_id="R1",
        procedure_type="STAR",
        waypoints=[{"name": "WP1", "x_nm": 10.0, "y_nm": 0.0, "speed_kt": 210}],
    )
    world = _world({"A1": ac}, rules)
    apply_actions(world, [{"aircraft": "A1", "type": "assign_speed", "speed_kt": 140}])
    assert ac.target_speed_kt == 140
    apply_actions(world, [{"aircraft": "A1", "type": "resume_procedure"}])
    assert ac.target_speed_kt is None


# --- takeoff roll ---

def test_takeoff_roll_accelerates_then_lifts_off():
    rules = RulesConfig(takeoff_roll_sec=30, takeoff_rotation_speed_kt=140.0, takeoff_acceleration_kt_per_sec=5.0)
    dep = Aircraft(callsign="D1", role="departure", x_nm=0.0, y_nm=0.0, altitude_ft=0, speed_kt=0, heading_deg=0, status="waiting_departure")
    world = _world({"D1": dep}, rules)
    apply_actions(world, [{"aircraft": "D1", "type": "clear_for_takeoff"}])
    assert dep.status == "rolling"
    assert dep.heading_deg == 90.0  # aligned with runway 09
    assert world.airport.runway_occupied_until_sec == 35  # max(occupancy, roll)

    _tick(world)
    assert dep.status == "rolling"
    assert dep.speed_kt == 25  # 5 kt/s * 5 s

    ticks_rolling = 1
    while dep.status == "rolling":
        _tick(world)
        ticks_rolling += 1
        assert ticks_rolling < 12
    # Lift-off: rotation speed, climbing toward the climb-out altitude.
    assert dep.status == "airborne_departure"
    assert dep.speed_kt == 140.0
    assert dep.vertical_rate_fpm == 2000
    assert dep.target_altitude_ft == 4000.0
    assert dep.takeoff_time_sec == 30


def test_legacy_instant_liftoff_without_roll_rules():
    dep = Aircraft(callsign="D1", role="departure", x_nm=0.0, y_nm=0.0, altitude_ft=0, speed_kt=0, heading_deg=90, status="waiting_departure")
    world = _world({"D1": dep})
    apply_actions(world, [{"aircraft": "D1", "type": "clear_for_takeoff"}])
    _tick(world)
    assert dep.status == "airborne_departure"
    assert dep.vertical_rate_fpm == 0  # legacy: no implied climb-out
