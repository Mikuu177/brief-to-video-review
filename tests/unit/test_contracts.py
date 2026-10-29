import pytest
from app.errors import ReviewError
from app.media.timeline import coverage_complete, global_range, threshold_uncertain, windows
from app.schemas.contracts import Locator, ObservationInput, ProjectInput
from app.services.aggregate import applicable, assessment, validate_checks
from app.services.reports import render_report
from app.services.versioning import content_hash
from conftest import rule_for
from pydantic import TypeAdapter, ValidationError


def test_platform_and_phase_required():
    with pytest.raises(ValidationError):
        ProjectInput(name="x", brand="x", product="x")


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "pdf", "page": 0},
        {"kind": "docx"},
        {"kind": "xlsx", "cell": "A1"},
        {"kind": "txt", "line_start": -1},
    ],
)
def test_source_locator_rejects_invalid_positions(locator):
    with pytest.raises(ValidationError):
        TypeAdapter(Locator).validate_python(locator)


def test_rule_scope_does_not_leak_between_materials_or_phases():
    rule = rule_for("b")
    assert applicable(rule, "xiaohongshu", "launch", "video")
    assert not applicable(rule, "douyin", "launch", "video")
    assert not applicable(rule, "xiaohongshu", "preheat", "video")
    rule["scope"]["asset_kinds"] = ["image_post"]
    assert not applicable(rule, "xiaohongshu", "launch", "video")
    rule["type"] = "example"
    assert not applicable(rule, "xiaohongshu", "launch", "image_post")


@pytest.mark.parametrize("duration", [1, 20000, 36133, 45000, 45001, 162963, 300000])
def test_continuous_windows_cover_entire_original(duration):
    intervals = windows(duration)
    assert intervals[0][0] == 0 and intervals[-1][1] == duration
    assert coverage_complete(intervals, duration)
    if duration > 45000:
        assert all(intervals[i - 1][1] - intervals[i][0] == 5000 for i in range(1, len(intervals)))


def test_163_second_windows_have_expected_boundaries():
    assert windows(162963) == [(0, 45000), (40000, 85000), (80000, 125000), (120000, 162963)]
    assert not coverage_complete([(0, 45000), (80000, 125000), (120000, 162963)], 162963)


def test_unknown_timing_error_is_not_zero():
    assert threshold_uncertain(14800, None, 15000)
    assert threshold_uncertain(14800, 300, 15000)
    assert not threshold_uncertain(14800, 50, 15000)
    assert not threshold_uncertain(15200, 50, 15000)


def test_segment_time_maps_to_original_and_bounds_are_checked():
    assert global_range(1200, 2000, {"actual_start_ms": 40000}, 162963) == (41200, 42000)
    with pytest.raises(ReviewError):
        global_range(1, 200000, {"actual_start_ms": 40000}, 162963)
    with pytest.raises(ValidationError):
        ObservationInput(channel="visual_event", content="x", start_ms=200, end_ms=100)


def test_segment_local_bounds_cannot_borrow_later_original_video():
    mapping = {"actual_start_ms": 40000, "actual_end_ms": 85000}
    assert global_range(1000, 45000, mapping, 162963) == (41000, 85000)
    with pytest.raises(ReviewError, match="当前连续片段"):
        global_range(1000, 46000, mapping, 162963)


def test_endpoint_rounding_is_explicit_and_limited_to_one_sampling_interval():
    mapping = {"actual_start_ms": 0, "actual_end_ms": 36133}
    with pytest.raises(ReviewError):
        global_range(30400, 36200, mapping, 36133)
    assert global_range(30400, 36200, mapping, 36133, 200) == (30400, 36133)
    with pytest.raises(ReviewError):
        global_range(30400, 36500, mapping, 36133, 200)
    with pytest.raises(ReviewError):
        global_range(36150, 36200, mapping, 36133, 200)


def test_incomplete_or_missing_rules_never_pass():
    rules = [rule_for("b")]
    checks = [{"rule_id": "R-001", "status": "PASS"}]
    assert assessment(rules, checks, False) == "NOT_READY"
    assert assessment(rules, [], True) == "NOT_READY"
    assert assessment(rules, checks, True) == "AI_PASSED"
    assert assessment(rules, checks, True, manual_required=True) == "NEEDS_HUMAN"
    assert assessment(rules, [{"rule_id": "R-001", "status": "FAIL"}], True) == "NEEDS_REVISION"
    assert assessment(rules, [{"rule_id": "R-001", "status": "UNCERTAIN"}], True) == "NEEDS_HUMAN"


def test_model_not_applicable_keeps_the_rule_as_uncertain():
    rules = [rule_for("source")]
    checks = [{"rule_id": "R-001", "status": "NOT_APPLICABLE", "evidence_ids": [], "limitations": []}]
    validate_checks(rules, checks, set())
    assert len(checks) == 1 and checks[0]["status"] == "UNCERTAIN"
    assert "NOT_APPLICABLE" in checks[0]["limitations"][0]
    assert assessment(rules, checks, True) == "NEEDS_HUMAN"


def test_missing_or_invented_evidence_is_not_accepted():
    rules = [rule_for("b")]
    check = {"rule_id": "R-001", "status": "PASS", "evidence_ids": [], "limitations": []}
    validate_checks(rules, [check], set())
    assert check["status"] == "UNCERTAIN"
    with pytest.raises(ReviewError):
        validate_checks(rules, [{**check, "evidence_ids": ["invented"]}], set())
    with pytest.raises(ReviewError):
        validate_checks(rules, [check, check], set())


def test_inputs_change_hash_including_config_and_cover():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"cover": "v1"}) != content_hash({"cover": "v2"})
    assert content_hash({"fps": 2}) != content_hash({"fps": 5})


def test_report_escapes_model_html():
    data = {
        "id": "r",
        "created_at": "now",
        "status": "INCOMPLETE",
        "assessment": "NOT_READY",
        "freshness": "CURRENT",
        "input_hash": "h",
        "input_snapshot": {"mode": "independent_human"},
        "rules": [],
        "checks": [],
        "evidence": [
            {"channel": "onscreen_text", "start_ms": 0, "end_ms": 10, "content": "<script>alert(1)</script>"}
        ],
        "decisions": [],
        "assets": [],
        "result": {},
    }
    html = render_report(data)
    assert "<script>" not in html and "&lt;script&gt;" in html
    data["kind"] = "video"
    data["input_snapshot"] = {"mode": "ai_assisted"}
    assert "旧版复核上下文记录" in render_report(data)
    data["input_snapshot"]["verification_context_version"] = 3
    assert "旧版复核上下文记录" not in render_report(data)
