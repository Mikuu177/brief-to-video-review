import copy

import pytest
from app.errors import ReviewError
from app.services.visual_inspection import CATEGORIES, validate_inspection


def response():
    return {
        "findings": [
            {"category": c, "status": "UNCERTAIN", "title": "待核对", "reason": "没有参考图"}
            for c in sorted(CATEGORIES)
        ],
        "limitations": ["5fps，可能遗漏瞬时信息"],
    }


def test_all_categories_remain_candidates():
    result = validate_inspection(response(), 1000)
    assert len(result["findings"]) == 5
    assert all(f["status"] == "UNCERTAIN" for f in result["findings"])


@pytest.mark.parametrize(
    "change",
    [
        {"status": "ISSUE"},
        {"start_ms": 0},
        {"start_ms": 100, "end_ms": 1100},
        {"start_ms": 400, "end_ms": 200},
        {"status": "AI_PASSED"},
        {"category": "beauty_score"},
    ],
)
def test_reject_unsupported_or_unbounded_observations(change):
    value = copy.deepcopy(response())
    value["findings"][0].update(change)
    with pytest.raises(ReviewError):
        validate_inspection(value, 1000)


def test_missing_category_rejected():
    value = response()
    value["findings"][-1] = value["findings"][0]
    with pytest.raises(ReviewError):
        validate_inspection(value, 1000)


def test_evidence_range_boundary_accepted():
    value = response()
    value["findings"][0].update(status="ISSUE", start_ms=0, end_ms=1000)
    assert validate_inspection(value, 1000)["findings"][0]["end_ms"] == 1000
