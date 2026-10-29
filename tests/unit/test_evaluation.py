import importlib.util

from conftest import PROJECT_ROOT

spec = importlib.util.spec_from_file_location("score", PROJECT_ROOT / "evaluation/score.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_confirmed_labels_yields_no_performance_claim():
    result = module.score([{"label_status": "proposed"}], [])
    assert result["recall"] is None and result["precision"] is None
    assert result["metric_status"] == "INSUFFICIENT_CONFIRMED_LABELS"
    uncertain = module.score([{"label_status": "confirmed", "expected_status": "UNCERTAIN"}], [])
    assert uncertain["confirmed_checks"] == 0 and uncertain["normal_controls"] == 0


def test_uncertain_is_a_miss_and_wrong_channel_is_not_true_positive():
    labels = [
        {
            "sample_id": f"M{i}",
            "rule_id": "R",
            "label_status": "confirmed",
            "expected_status": "FAIL",
            "channel": "spoken_audio",
            "start_ms": 1000,
            "end_ms": 2000,
            "severity": "blocker",
        }
        for i in (1, 2)
    ]
    predictions = [
        {"sample_id": "M1", "rule_id": "R", "status": "UNCERTAIN", "assessment": "NEEDS_HUMAN"},
        {
            "sample_id": "M2",
            "rule_id": "R",
            "status": "FAIL",
            "channel": "onscreen_text",
            "start_ms": 1000,
            "end_ms": 2000,
            "assessment": "AI_PASSED",
        },
    ]
    result = module.score(labels, predictions)
    assert result["tp"] == 0 and result["fn"] == 2 and result["recall"] == 0
    assert result["uncertainty_ratio"] == 0.5
    assert result["blocking_submissions_incorrectly_passed"] == 1
