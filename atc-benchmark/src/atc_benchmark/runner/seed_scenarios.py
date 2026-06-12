"""Procedural scenario (level) generator.

Generates deterministic scenario JSONs from named templates, seeded by an RNG
seed so the same invocation always produces the same files. Every generated
scenario carries complete ``scenario_metadata`` (description, tags,
difficulty tier, stressors, star thresholds, expected baseline ranges, and
generator provenance) and is checked with ``validate_scenario_document``
before being written.

Usage:
    atc-seed --template arrival_rush --tier intro --count 2 --seed 7
    atc-seed --pack starter --seed 42 --out scenarios
"""

from __future__ import annotations

import argparse
import json
import random
from math import cos, hypot, radians, sin
from pathlib import Path
from typing import Any

from atc_benchmark.paths import scenarios_dir
from atc_benchmark.simulator.scenario_validation import validate_scenario_document

GENERATOR_NAME = "atc-seed"
GENERATOR_VERSION = "1.0.0"

TEMPLATES = ("arrival_rush", "crossing_conflict", "departure_pressure", "emergency_inbound", "wind_shift")
TIERS = ("tutorial", "intro", "intermediate", "advanced", "expert")

# Tier knobs: traffic volume and how forgiving the star cutoffs are.
TIER_PROFILE: dict[str, dict[str, Any]] = {
    "tutorial": {"arrivals": (1, 1), "departures": (0, 1), "star_thresholds": [1.0, 50.0, 80.0]},
    "intro": {"arrivals": (2, 2), "departures": (0, 1), "star_thresholds": [1.0, 55.0, 82.0]},
    "intermediate": {"arrivals": (2, 3), "departures": (1, 1), "star_thresholds": [1.0, 60.0, 85.0]},
    "advanced": {"arrivals": (3, 4), "departures": (1, 2), "star_thresholds": [1.0, 65.0, 88.0]},
    "expert": {"arrivals": (4, 5), "departures": (2, 2), "star_thresholds": [1.0, 70.0, 90.0]},
}

AIRCRAFT_TYPES = [
    ("B738", "medium"),
    ("A320", "medium"),
    ("A321", "medium"),
    ("E190", "light"),
    ("B77W", "heavy"),
    ("A359", "heavy"),
]
AIRLINE_CODES = ["UAL", "DAL", "AAL", "SWA", "JBU", "ASA", "FDX", "UPS", "NKS", "FFT"]


def _rotate(x: float, y: float, runway_heading_deg: float) -> tuple[float, float]:
    """Rotate a point authored for runway 09 (heading 090, along +x) to the given runway heading."""
    h = radians(runway_heading_deg)
    return (
        round(x * sin(h) - y * cos(h), 3),
        round(x * cos(h) + y * sin(h), 3),
    )


def _airport_layout(runway_id: str) -> dict[str, Any]:
    heading = int(runway_id) * 10
    def rp(x: float, y: float) -> dict[str, float]:
        rx, ry = _rotate(x, y, heading)
        return {"x_nm": rx, "y_nm": ry}

    return {
        "runways": [{"id": runway_id, "ends": [rp(-2.6, 0.0), rp(2.6, 0.0)], "width_nm": 0.08}],
        "taxiways": [
            {"id": "A", "points": [rp(-1.8, -0.35), rp(-0.4, -0.35), rp(0.0, -0.1), rp(1.4, -0.1)], "width_nm": 0.04},
            {"id": "B", "points": [rp(0.0, -0.1), rp(0.0, -0.8)], "width_nm": 0.04},
        ],
        "aprons": [{"id": "MAIN", "polygon": [rp(-0.9, -1.15), rp(0.9, -1.15), rp(0.9, -0.72), rp(-0.9, -0.72)]}],
        "stands": [
            {"id": "S1", "position": rp(-0.55, -0.94)},
            {"id": "S2", "position": rp(0.0, -0.94)},
            {"id": "S3", "position": rp(0.55, -0.94)},
        ],
    }


class ScenarioFactory:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used_callsigns: set[str] = set()

    def callsign(self) -> str:
        while True:
            candidate = f"{self.rng.choice(AIRLINE_CODES)}{self.rng.randint(100, 999)}"
            if candidate not in self.used_callsigns:
                self.used_callsigns.add(candidate)
                return candidate

    def runway(self) -> str:
        return f"{self.rng.randint(1, 36):02d}"

    def arrival(self, runway_id: str, *, bearing_deg: float, distance_nm: float, altitude_ft: float, speed_kt: float) -> dict[str, Any]:
        ac_type, wake = self.rng.choice(AIRCRAFT_TYPES)
        x = round(distance_nm * sin(radians(bearing_deg)), 3)
        y = round(distance_nm * cos(radians(bearing_deg)), 3)
        return {
            "callsign": self.callsign(),
            "role": "arrival",
            "x_nm": x,
            "y_nm": y,
            "altitude_ft": round(altitude_ft),
            "speed_kt": round(speed_kt),
            "heading_deg": round(bearing_deg + 180) % 360,
            "status": "airborne",
            "target_runway": runway_id,
            "aircraft_type": ac_type,
            "wake_category": wake,
        }

    # Hold-short points staggered back along taxiway A so queued departures never overlap.
    DEPARTURE_HOLD_POINTS = [(0.0, -0.28), (-0.7, -0.35), (-1.4, -0.35)]

    def departure(self, runway_id: str, queue_index: int = 0) -> dict[str, Any]:
        ac_type, wake = self.rng.choice(AIRCRAFT_TYPES)
        heading = int(runway_id) * 10
        hold = self.DEPARTURE_HOLD_POINTS[min(queue_index, len(self.DEPARTURE_HOLD_POINTS) - 1)]
        x, y = _rotate(*hold, heading)
        return {
            "callsign": self.callsign(),
            "role": "departure",
            "x_nm": x,
            "y_nm": y,
            "altitude_ft": 0,
            "speed_kt": 0,
            "heading_deg": heading % 360,
            "status": "waiting_departure",
            "target_runway": runway_id,
            "aircraft_type": ac_type,
            "wake_category": wake,
        }

    def arrival_stream(
        self,
        runway_id: str,
        count: int,
        *,
        spread_deg: float = 360.0,
        eta_gap_sec: tuple[int, int] = (90, 180),
        base_altitude_ft: float = 3000.0,
    ) -> list[dict[str, Any]]:
        """Arrivals with staggered unmanaged ETAs, vertically stratified by >=1000 ft."""
        arrivals = []
        eta_sec = self.rng.uniform(140, 220)
        base_bearing = self.rng.uniform(0, 360)
        for i in range(count):
            bearing = (base_bearing + self.rng.uniform(0, spread_deg)) % 360
            speed = self.rng.uniform(180, 230)
            distance = max(5.0, min(16.0, speed * eta_sec / 3600.0))
            arrivals.append(
                self.arrival(
                    runway_id,
                    bearing_deg=bearing,
                    distance_nm=round(distance, 2),
                    altitude_ft=base_altitude_ft + i * 1200 + self.rng.uniform(-100, 100),
                    speed_kt=speed,
                )
            )
            eta_sec += self.rng.uniform(*eta_gap_sec)
        return arrivals


def _base_scenario(runway_id: str, aircraft: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    heading = int(runway_id) * 10
    return {
        "tick_sec": 5,
        "airport": {
            "runway_id": runway_id,
            "active_runway": runway_id,
            "runway_occupied_by": None,
            "departure_queue": [a["callsign"] for a in aircraft if a["status"] == "waiting_departure"],
            "layout": _airport_layout(runway_id),
        },
        "weather": {
            "wind_dir_deg": round(heading + rng.uniform(-20, 20)) % 360,
            "wind_speed_kt": round(rng.uniform(4, 12)),
        },
        "rules": {"min_horizontal_nm": 3.0, "min_vertical_ft": 1000.0, "lookahead_seconds": 90},
        "aircraft": aircraft,
    }


def _build_arrival_rush(factory: ScenarioFactory, tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = factory.rng
    profile = TIER_PROFILE[tier]
    runway = factory.runway()
    arrivals = factory.arrival_stream(runway, rng.randint(*profile["arrivals"]), eta_gap_sec=(70, 140))
    departures = [factory.departure(runway, i) for i in range(rng.randint(*profile["departures"]))]
    scenario = _base_scenario(runway, arrivals + departures, rng)
    meta = {
        "description": (
            f"A steady inbound push: {len(arrivals)} arrival(s) converge on runway {runway}"
            + (f" while {len(departures)} departure(s) wait for a gap" if departures else "")
            + ". Sequence the flow without losing separation."
        ),
        "tags": ["efficiency", "safety"] if departures else ["efficiency"],
        "intended_stressors": ["arrival sequencing under volume"] + (["departure gap management"] if departures else []),
    }
    return scenario, meta


def _build_crossing_conflict(factory: ScenarioFactory, tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = factory.rng
    profile = TIER_PROFILE[tier]
    runway = factory.runway()
    # Two arrivals on crossing tracks with near-simultaneous ETAs and <1000 ft separation.
    eta_sec = rng.uniform(150, 220)
    bearing_a = rng.uniform(0, 360)
    bearing_b = (bearing_a + rng.uniform(70, 110)) % 360
    speed_a, speed_b = rng.uniform(195, 220), rng.uniform(195, 220)
    altitude = rng.uniform(2800, 3400)
    pair = [
        factory.arrival(runway, bearing_deg=bearing_a, distance_nm=round(speed_a * eta_sec / 3600.0, 2), altitude_ft=altitude, speed_kt=speed_a),
        factory.arrival(
            runway,
            bearing_deg=bearing_b,
            distance_nm=round(speed_b * (eta_sec + rng.uniform(-15, 15)) / 3600.0, 2),
            altitude_ft=altitude + rng.uniform(-200, 200),
            speed_kt=speed_b,
        ),
    ]
    # Extra traffic flies well above the engineered conflict pair.
    extra_arrivals = factory.arrival_stream(runway, max(0, rng.randint(*profile["arrivals"]) - 2), base_altitude_ft=4600)
    departures = [factory.departure(runway, i) for i in range(rng.randint(*profile["departures"]))]
    scenario = _base_scenario(runway, pair + extra_arrivals + departures, rng)
    meta = {
        "description": (
            f"{pair[0]['callsign']} and {pair[1]['callsign']} are converging on crossing tracks at similar altitude "
            f"with nearly identical ETAs to runway {runway}. Break the conflict early, then rebuild the sequence."
        ),
        "tags": ["safety"],
        "intended_stressors": ["crossing-arrival geometry", "short conflict horizon"],
    }
    return scenario, meta


def _build_departure_pressure(factory: ScenarioFactory, tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = factory.rng
    profile = TIER_PROFILE[tier]
    runway = factory.runway()
    arrivals = factory.arrival_stream(runway, rng.randint(*profile["arrivals"]), eta_gap_sec=(100, 170))
    departures = [factory.departure(runway, i) for i in range(max(1, rng.randint(*profile["departures"])))]
    scenario = _base_scenario(runway, arrivals + departures, rng)
    meta = {
        "description": (
            f"{len(departures)} departure(s) are holding short of runway {runway} with {len(arrivals)} arrival(s) "
            "inbound. Launch the queue through arrival gaps without forcing a go-around."
        ),
        "tags": ["efficiency", "safety"],
        "intended_stressors": ["runway slot allocation", "departure release timing between arrivals"],
    }
    return scenario, meta


def _build_emergency_inbound(factory: ScenarioFactory, tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = factory.rng
    profile = TIER_PROFILE[tier]
    runway = factory.runway()
    arrivals = factory.arrival_stream(runway, max(2, rng.randint(*profile["arrivals"])))
    departures = [factory.departure(runway, i) for i in range(rng.randint(*profile["departures"]))]
    scenario = _base_scenario(runway, arrivals + departures, rng)

    subject = rng.choice(arrivals)
    event_time = rng.randint(2, 8) * 5
    subtype = rng.choice(["low_fuel_emergency", "engine_failure"])
    if subtype == "low_fuel_emergency":
        distance_nm = hypot(subject["x_nm"], subject["y_nm"])
        eta_sec = distance_nm / subject["speed_kt"] * 3600
        event: dict[str, Any] = {
            "time_sec": event_time,
            "type": "low_fuel_emergency",
            "aircraft": subject["callsign"],
            "remaining_endurance_sec": int(eta_sec + rng.uniform(180, 360)),
        }
        stressor = "fuel-limited priority landing"
        what = "declares minimum fuel"
    else:
        event = {
            "time_sec": event_time,
            "type": "engine_failure",
            "aircraft": subject["callsign"],
            "require_return_to_land": True,
        }
        stressor = "degraded-performance emergency return"
        what = "loses an engine and needs priority handling"
    scenario["events"] = [event]
    meta = {
        "description": (
            f"{subject['callsign']} {what} shortly after you take the position, with "
            f"{len(arrivals) - 1 + len(departures)} other aircraft to manage. Get the emergency on the ground first."
        ),
        "tags": ["safety", "event"],
        "intended_stressors": [stressor, "priority resequencing under traffic"],
    }
    return scenario, meta


def _build_wind_shift(factory: ScenarioFactory, tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rng = factory.rng
    profile = TIER_PROFILE[tier]
    runway = factory.runway()
    arrivals = factory.arrival_stream(runway, rng.randint(*profile["arrivals"]))
    departures = [factory.departure(runway, i) for i in range(rng.randint(*profile["departures"]))]
    scenario = _base_scenario(runway, arrivals + departures, rng)
    heading = int(runway) * 10
    event_time = rng.randint(3, 10) * 5
    scenario["events"] = [
        {
            "time_sec": event_time,
            "type": "wind_change",
            "wind_dir_deg": round(heading + 180 + rng.uniform(-25, 25)) % 360,
            "wind_speed_kt": round(rng.uniform(14, 22)),
        }
    ]
    meta = {
        "description": (
            f"The wind swings through 180 degrees at t+{event_time}s, turning runway {runway} into a tailwind "
            "operation mid-push. Reconfigure the runway and re-sequence everyone already established."
        ),
        "tags": ["event", "safety"],
        "intended_stressors": ["wind shift", "runway reconfiguration response"],
    }
    return scenario, meta


BUILDERS = {
    "arrival_rush": _build_arrival_rush,
    "crossing_conflict": _build_crossing_conflict,
    "departure_pressure": _build_departure_pressure,
    "emergency_inbound": _build_emergency_inbound,
    "wind_shift": _build_wind_shift,
}

# Broad envelopes batch evaluation uses to flag drift, not strict gates.
def _expected_baseline_ranges(tier: str, has_event: bool) -> dict[str, list[float]]:
    ranges: dict[str, list[float]] = {
        "score": [0.0, 110.0],
        "active_conflicts_count_total": [0.0, 400.0],
        "throughput_ops_per_hour": [0.0, 80.0],
    }
    if has_event:
        ranges["emergency_unhandled_count"] = [0.0, 1.0]
    return ranges


def generate_scenario(template: str, tier: str, seed: int) -> dict[str, Any]:
    """Build one fully-validated scenario document for (template, tier, seed)."""
    rng = random.Random(f"{template}:{tier}:{seed}")
    factory = ScenarioFactory(rng)
    scenario, meta = BUILDERS[template](factory, tier)
    scenario["scenario_metadata"] = {
        "description": meta["description"],
        "tags": meta["tags"],
        "difficulty_tier": tier,
        "intended_stressors": meta["intended_stressors"],
        "star_thresholds": TIER_PROFILE[tier]["star_thresholds"],
        "expected_baseline_ranges": _expected_baseline_ranges(tier, bool(scenario.get("events"))),
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "template": template,
            "seed": seed,
        },
    }
    validate_scenario_document(scenario, f"<generated:{template}/{tier}/seed={seed}>")
    return scenario


STARTER_PACK = [
    ("arrival_rush", "intro"),
    ("arrival_rush", "intermediate"),
    ("crossing_conflict", "intermediate"),
    ("crossing_conflict", "advanced"),
    ("departure_pressure", "intro"),
    ("departure_pressure", "advanced"),
    ("emergency_inbound", "intermediate"),
    ("emergency_inbound", "expert"),
    ("wind_shift", "advanced"),
    ("wind_shift", "expert"),
]


def _next_index(out_dir: Path, stem_prefix: str) -> int:
    existing = [int(p.stem.rsplit("_", 1)[-1]) for p in out_dir.glob(f"{stem_prefix}_*.json") if p.stem.rsplit("_", 1)[-1].isdigit()]
    return max(existing, default=0) + 1


def write_scenarios(specs: list[tuple[str, str]], seed: int, out_dir: Path, *, overwrite: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for offset, (template, tier) in enumerate(specs):
        scenario = generate_scenario(template, tier, seed + offset)
        stem_prefix = f"gen_{template}_{tier}"
        path = out_dir / f"{stem_prefix}_{_next_index(out_dir, stem_prefix):03d}.json"
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        path.write_text(json.dumps(scenario, indent=2) + "\n")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed procedurally generated ATC scenarios with full metadata.")
    parser.add_argument("--template", choices=TEMPLATES, help="Generate scenarios from a single template")
    parser.add_argument("--tier", choices=TIERS, default="intermediate", help="Difficulty tier for --template mode")
    parser.add_argument("--count", type=int, default=1, help="How many variants to generate in --template mode")
    parser.add_argument("--pack", choices=["starter"], help="Generate a curated multi-template level pack")
    parser.add_argument("--seed", type=int, default=0, help="Base RNG seed; same seed reproduces the same scenarios")
    parser.add_argument("--out", default=str(scenarios_dir()), help="Output directory (default: package scenarios dir)")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print what would be written, without writing")
    args = parser.parse_args()

    if bool(args.template) == bool(args.pack):
        parser.error("choose exactly one of --template or --pack")

    specs = STARTER_PACK if args.pack else [(args.template, args.tier)] * args.count
    if args.dry_run:
        for offset, (template, tier) in enumerate(specs):
            scenario = generate_scenario(template, tier, args.seed + offset)
            n_aircraft = len(scenario["aircraft"])
            print(f"ok: {template}/{tier} seed={args.seed + offset} aircraft={n_aircraft} events={len(scenario.get('events', []))}")
        return

    for path in write_scenarios(specs, args.seed, Path(args.out)):
        print(f"wrote: {path}")


if __name__ == "__main__":
    main()
