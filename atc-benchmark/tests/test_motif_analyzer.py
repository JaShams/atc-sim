import json
from pathlib import Path

from atc_benchmark.runner.analyze_motifs import diff_summaries, summarize_motifs


def _write_trace(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_summarize_motifs_groups_and_ranks(tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _write_trace(
        trace_dir / "a.jsonl",
        [
            {
                "actions": [{"type": "clear_for_takeoff"}],
                "tick_explanation": {
                    "call_reason": {"type": "event"},
                    "outcome": {"window_delta": -0.20},
                    "score_delta_by_component": {"invalid_command": -5.0, "loss_of_separation": 0.0},
                },
            },
            {
                "actions": [{"type": "clear_for_takeoff"}],
                "tick_explanation": {
                    "call_reason": {"type": "event"},
                    "outcome": {"window_delta": -0.30},
                    "score_delta_by_component": {"invalid_command": -4.0},
                },
            },
            {
                "actions": [{"type": "assign_heading"}],
                "tick_explanation": {
                    "call_reason": {"type": "decision_point"},
                    "outcome": {"window_delta": -0.10},
                    "score_delta_by_component": {"loss_of_separation": -8.0},
                },
            },
        ],
    )

    summary = summarize_motifs(trace_dir, top_n=5)
    assert summary["motif_count"] == 2
    top = summary["top_motifs"][0]
    assert top["trigger_type"] == "event"
    assert top["action_type"] == "clear_for_takeoff"
    assert top["dominant_negative_score_component"] == "invalid_command"
    assert top["count"] == 2


def test_diff_summaries(tmp_path):
    baseline = {
        "trace_dir": "base",
        "top_motifs": [
            {
                "trigger_type": "event",
                "action_type": "clear_for_takeoff",
                "dominant_negative_score_component": "invalid_command",
                "count": 2,
                "mean_impact": -0.2,
            }
        ],
    }
    branch = {
        "trace_dir": "branch",
        "top_motifs": [
            {
                "trigger_type": "event",
                "action_type": "clear_for_takeoff",
                "dominant_negative_score_component": "invalid_command",
                "count": 3,
                "mean_impact": -0.35,
            }
        ],
    }

    diff = diff_summaries(baseline, branch)
    assert diff["motif_deltas"][0]["count_delta"] == 1
    assert diff["motif_deltas"][0]["mean_impact_delta"] == -0.14999999999999997
