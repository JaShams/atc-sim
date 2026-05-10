from pathlib import Path

from atc_benchmark.agents.heuristic_agent import HeuristicAgent
from atc_benchmark.agents.noop_agent import NoOpAgent
from atc_benchmark.agents.random_valid_action_agent import RandomValidActionAgent
from atc_benchmark.simulator.engine import load_world, run


KEY_SCENARIOS = [
    "departure_between_arrivals_001.json",
    "wind_change_runway_switch_001.json",
]


def _run_score(scenario_name: str, agent, tmp_path) -> dict:
    world = load_world(Path("scenarios") / scenario_name)
    return run(world, agent, max_ticks=60, trace_path=tmp_path / f"{scenario_name}-{agent.__class__.__name__}.jsonl")


def test_heuristic_avoids_contradictory_commands_per_tick():
    agent = HeuristicAgent()
    obs = {
        "snapshot": {
            "airport": {"runway_occupied_by": None},
            "aircraft": {"ARR1": {}, "DEP1": {}},
        },
        "decision_points": [
            {"type": "predicted_conflict", "aircraft": ["ARR1"]},
            {"type": "active_conflict", "aircraft": ["ARR1"]},
            {"type": "emergency", "aircraft": ["ARR1"]},
            {"type": "departure_ready", "aircraft": ["DEP1"]},
        ],
    }

    out = agent.act(obs)
    actions = out["actions"]
    controlled = [a["aircraft"] for a in actions if a["type"] != "no_op"]
    assert len(controlled) == len(set(controlled))
    assert actions[0]["type"] == "clear_to_land"


def test_heuristic_baseline_outperforms_noop_and_random_on_key_scenarios(tmp_path):
    baseline_score = 0.0
    noop_score = 0.0
    random_score = 0.0
    baseline_invalids = 0
    noop_invalids = 0
    random_invalids = 0

    for scenario in KEY_SCENARIOS:
        baseline = _run_score(scenario, HeuristicAgent(), tmp_path)
        noop = _run_score(scenario, NoOpAgent(), tmp_path)
        random = _run_score(scenario, RandomValidActionAgent(seed=0), tmp_path)

        baseline_score += baseline["score"]
        noop_score += noop["score"]
        random_score += random["score"]
        baseline_invalids += baseline["control_quality"]["invalid_commands"]
        noop_invalids += noop["control_quality"]["invalid_commands"]
        random_invalids += random["control_quality"]["invalid_commands"]

    assert baseline_score > noop_score
    assert baseline_score > random_score
    assert baseline_invalids <= random_invalids
    assert baseline_invalids <= noop_invalids
