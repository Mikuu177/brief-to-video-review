"""Score confirmed rule/evidence labels only. Undefined metrics remain null."""

import argparse
import json
from pathlib import Path


def score(labels: list[dict], predictions: list[dict]) -> dict:
    confirmed = [
        item
        for item in labels
        if item.get("label_status") == "confirmed" and item.get("expected_status") in ("PASS", "FAIL")
    ]
    predicted = {(p["sample_id"], p["rule_id"]): p for p in predictions}
    tp = fp = fn = uncertain = controls = 0
    missed_blocking_submissions = set()
    for item in confirmed:
        key = (item["sample_id"], item["rule_id"])
        result = predicted.get(key, {})
        positive = item["expected_status"] == "FAIL"
        failed = result.get("status") == "FAIL"
        # Matching a vague warning is insufficient: rule, channel, and bounded evidence must match.
        evidence_matches = result.get("channel") == item.get("channel")
        if item.get("start_ms") is not None:
            start, end = result.get("start_ms"), result.get("end_ms")
            evidence_matches = (
                evidence_matches
                and start is not None
                and end is not None
                and (
                    start <= item["start_ms"]
                    and end >= item["end_ms"]
                    and end - start <= (item["end_ms"] - item["start_ms"]) + 6000
                )
            )
        if positive:
            if result.get("assessment") == "AI_PASSED" and item.get("severity") == "blocker":
                missed_blocking_submissions.add(item["sample_id"])
            if failed and evidence_matches:
                tp += 1
            else:
                fn += 1
                if failed:
                    fp += 1
        else:
            controls += 1
            if failed:
                fp += 1
        if result.get("status") == "UNCERTAIN":
            uncertain += 1
    count = len(confirmed)
    return {
        "confirmed_checks": count,
        "normal_controls": controls,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "recall": tp / (tp + fn) if tp + fn else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "uncertainty_ratio": uncertain / count if count else None,
        "blocking_submissions_incorrectly_passed": len(missed_blocking_submissions) if count else None,
        "sample_size_sufficient": count >= 60 and controls >= 20,
        "metric_status": "MEASURED" if count else "INSUFFICIENT_CONFIRMED_LABELS",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    labels = [json.loads(line) for line in args.labels.read_text().splitlines() if line.strip()]
    result = score(labels, json.loads(args.predictions.read_text()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, indent=2))
