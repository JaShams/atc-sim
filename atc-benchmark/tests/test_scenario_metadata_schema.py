
from atc_benchmark.paths import scenarios_dir

REQUIRED_TOP_LEVEL_KEYS = {"tags", "difficulty_tier", "intended_stressors", "expected_baseline_ranges"}
ALLOWED_TAGS = {"safety", "efficiency", "event"}
ALLOWED_TIERS = {"intro", "intermediate", "advanced", "expert"}


def test_scenario_metadata_is_complete():
    scenario_dir = scenarios_dir()
    for scenario_file in sorted(scenario_dir.glob("*.json")):
        payload = __import__("json").loads(scenario_file.read_text())
        assert "scenario_metadata" in payload, f"missing scenario_metadata in {scenario_file.name}"
        metadata = payload["scenario_metadata"]

        missing = REQUIRED_TOP_LEVEL_KEYS - metadata.keys()
        assert not missing, f"missing metadata keys in {scenario_file.name}: {sorted(missing)}"

        tags = metadata["tags"]
        assert isinstance(tags, list) and tags, f"tags must be a non-empty list in {scenario_file.name}"
        assert set(tags).issubset(ALLOWED_TAGS), f"unknown tag in {scenario_file.name}: {tags}"

        tier = metadata["difficulty_tier"]
        assert tier in ALLOWED_TIERS, f"invalid tier in {scenario_file.name}: {tier}"

        stressors = metadata["intended_stressors"]
        assert isinstance(stressors, list) and stressors, f"intended_stressors must be a non-empty list in {scenario_file.name}"
        assert all(isinstance(stressor, str) and stressor.strip() for stressor in stressors)

        ranges = metadata["expected_baseline_ranges"]
        assert isinstance(ranges, dict) and ranges, f"expected_baseline_ranges must be a non-empty object in {scenario_file.name}"
        for metric_name, baseline_range in ranges.items():
            assert isinstance(metric_name, str) and metric_name.strip()
            assert isinstance(baseline_range, list) and len(baseline_range) == 2, (
                f"baseline range must have [min, max] in {scenario_file.name}: {metric_name}"
            )
            lower, upper = baseline_range
            assert isinstance(lower, (int, float)) and isinstance(upper, (int, float))
            assert lower <= upper, f"baseline range min must be <= max in {scenario_file.name}: {metric_name}"
