from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def _iter_trace_events(trace_dir: Path):
    for trace_path in sorted(trace_dir.glob("*.jsonl")):
        with trace_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _dominant_negative_component(score_delta_by_component: dict[str, float]) -> str:
    negatives = [(k, v) for k, v in score_delta_by_component.items() if v < 0]
    if not negatives:
        return "none"
    negatives.sort(key=lambda kv: kv[1])
    return negatives[0][0]


def _action_type(actions: list[dict]) -> str:
    if not actions:
        return "no_op"
    first = actions[0]
    return first.get("type", "unknown") if isinstance(first, dict) else "unknown"


def _ci95(samples: list[float]) -> tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    mean = sum(samples) / len(samples)
    if len(samples) == 1:
        return mean, mean
    variance = sum((x - mean) ** 2 for x in samples) / (len(samples) - 1)
    margin = 1.96 * math.sqrt(variance / len(samples))
    return mean - margin, mean + margin


def summarize_motifs(trace_dir: Path, top_n: int = 10) -> dict:
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for event in _iter_trace_events(trace_dir):
        explanation = event.get("tick_explanation", {})
        outcome = explanation.get("outcome", {})
        impact = outcome.get("window_delta")
        if impact is None:
            impact = outcome.get("immediate_delta", 0.0)
        trigger_type = explanation.get("call_reason", {}).get("type", "none")
        action_type = _action_type(event.get("actions") or [])
        dominant_negative = _dominant_negative_component(explanation.get("score_delta_by_component", {}))

        key = (trigger_type, action_type, dominant_negative)
        grouped[key].append(float(impact))

    motifs = []
    for (trigger_type, action_type, dominant_negative), impacts in grouped.items():
        mean_impact = sum(impacts) / len(impacts)
        ci_low, ci_high = _ci95(impacts)
        motifs.append(
            {
                "trigger_type": trigger_type,
                "action_type": action_type,
                "dominant_negative_score_component": dominant_negative,
                "count": len(impacts),
                "mean_impact": mean_impact,
                "ci95_low": ci_low,
                "ci95_high": ci_high,
            }
        )

    def _sort_key(motif: dict) -> tuple[float, int]:
        mean_impact = motif.get("mean_impact", 0.0)
        count = motif.get("count", 0)
        return (mean_impact if isinstance(mean_impact, int | float) else 0.0, -(count if isinstance(count, int) else 0))

    motifs.sort(key=_sort_key)
    top = motifs[:top_n]
    return {"trace_dir": str(trace_dir), "motif_count": len(motifs), "top_motifs": top}


def diff_summaries(baseline: dict, branch: dict) -> dict:
    def _index(summary: dict) -> dict[tuple[str, str, str], dict]:
        index = {}
        for motif in summary.get("top_motifs", []):
            k = (
                motif["trigger_type"],
                motif["action_type"],
                motif["dominant_negative_score_component"],
            )
            index[k] = motif
        return index

    base_index = _index(baseline)
    branch_index = _index(branch)
    keys = sorted(set(base_index) | set(branch_index))
    rows = []
    for key in keys:
        b = base_index.get(key)
        n = branch_index.get(key)
        rows.append(
            {
                "trigger_type": key[0],
                "action_type": key[1],
                "dominant_negative_score_component": key[2],
                "baseline_count": b["count"] if b else 0,
                "branch_count": n["count"] if n else 0,
                "count_delta": (n["count"] if n else 0) - (b["count"] if b else 0),
                "baseline_mean_impact": b["mean_impact"] if b else 0.0,
                "branch_mean_impact": n["mean_impact"] if n else 0.0,
                "mean_impact_delta": (n["mean_impact"] if n else 0.0) - (b["mean_impact"] if b else 0.0),
            }
        )
    rows.sort(key=lambda r: r["mean_impact_delta"])
    return {"baseline": baseline.get("trace_dir"), "branch": branch.get("trace_dir"), "motif_deltas": rows}


def _print_motifs(summary: dict) -> None:
    print(f"Motif summary for {summary['trace_dir']} ({summary['motif_count']} motifs)")
    for i, motif in enumerate(summary["top_motifs"], start=1):
        print(
            f"{i:>2}. trigger={motif['trigger_type']:<15} action={motif['action_type']:<20} "
            f"dominant_neg={motif['dominant_negative_score_component']:<30} "
            f"count={motif['count']:<4} mean={motif['mean_impact']:.4f} "
            f"ci95=[{motif['ci95_low']:.4f},{motif['ci95_high']:.4f}]"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--output", help="Optional path for JSON output.")
    parser.add_argument("--baseline", help="Path to baseline motif summary JSON for diff mode.")
    parser.add_argument("--branch", help="Path to branch motif summary JSON for diff mode.")
    args = parser.parse_args()

    if args.baseline and args.branch:
        baseline = json.loads(Path(args.baseline).read_text())
        branch = json.loads(Path(args.branch).read_text())
        diff = diff_summaries(baseline, branch)
        if args.output:
            Path(args.output).write_text(json.dumps(diff, indent=2))
        print(json.dumps(diff, indent=2))
        return

    if not args.trace_dir:
        raise SystemExit("--trace-dir is required unless --baseline and --branch are provided")

    summary = summarize_motifs(Path(args.trace_dir), top_n=args.top_n)
    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2))
    _print_motifs(summary)


if __name__ == "__main__":
    main()
