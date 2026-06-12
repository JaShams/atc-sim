import json

from atc_benchmark.runner.seed_scenarios import (
    STARTER_PACK,
    TEMPLATES,
    TIERS,
    generate_scenario,
    write_scenarios,
)
from atc_benchmark.simulator.engine import load_world

REQUIRED_METADATA_KEYS = {
    "description",
    "tags",
    "difficulty_tier",
    "intended_stressors",
    "star_thresholds",
    "expected_baseline_ranges",
    "generator",
}


def test_every_template_tier_combo_generates_valid_scenarios():
    for template in TEMPLATES:
        for tier in TIERS:
            scenario = generate_scenario(template, tier, seed=123)
            metadata = scenario["scenario_metadata"]
            assert REQUIRED_METADATA_KEYS <= metadata.keys()
            assert metadata["description"].strip()
            assert metadata["difficulty_tier"] == tier
            assert metadata["generator"]["template"] == template
            assert metadata["generator"]["seed"] == 123
            assert len(metadata["star_thresholds"]) == 3


def test_generation_is_deterministic_for_same_seed():
    a = generate_scenario("crossing_conflict", "advanced", seed=7)
    b = generate_scenario("crossing_conflict", "advanced", seed=7)
    c = generate_scenario("crossing_conflict", "advanced", seed=8)
    assert a == b
    assert a != c


def test_generated_scenarios_load_into_engine(tmp_path):
    for template in TEMPLATES:
        scenario = generate_scenario(template, "expert", seed=5)
        path = tmp_path / f"{template}.json"
        path.write_text(json.dumps(scenario))
        world = load_world(path)
        assert world.aircraft


def test_departure_queue_positions_do_not_overlap():
    scenario = generate_scenario("departure_pressure", "expert", seed=11)
    waiting = [a for a in scenario["aircraft"] if a["status"] == "waiting_departure"]
    positions = {(a["x_nm"], a["y_nm"]) for a in waiting}
    assert len(positions) == len(waiting)


def test_write_scenarios_emits_files_with_metadata(tmp_path):
    written = write_scenarios(STARTER_PACK[:2], seed=42, out_dir=tmp_path)
    assert len(written) == 2
    for path in written:
        doc = json.loads(path.read_text())
        assert REQUIRED_METADATA_KEYS <= doc["scenario_metadata"].keys()
