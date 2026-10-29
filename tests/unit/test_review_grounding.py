from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.errors import ReviewError
from app.workers.review import (
    checked_rules,
    existing_verification_reference,
    grounded_check_references,
    grounded_conflicts,
    model_observation,
    persist_checks,
    verification_evidence,
    verification_payload,
    video_format,
)
from conftest import rule_for


def test_script_preflight_includes_video_plans_without_image_post_or_caption_requirements():
    rules = []
    for kind in ["script", "video", "image_post", "caption", "cover"]:
        rule = rule_for("source", id=kind)
        rule["scope"]["asset_kinds"] = [kind]
        rules.append(rule)
    db = SimpleNamespace(get=lambda *args: SimpleNamespace(rules=rules))
    project = SimpleNamespace(platform="xiaohongshu", phase="launch")
    run = SimpleNamespace(kind="script", ruleset_id="ruleset")
    assert [r["id"] for r in checked_rules(db, run, None, project)] == ["script", "video"]
    assert rules[1]["scope"]["asset_kinds"] == ["video"]


@pytest.mark.parametrize("channel", ["audio_observed", "audio_event", "human_observation"])
def test_model_cannot_invent_channel_or_impersonate_human(channel):
    with pytest.raises(ReviewError) as error:
        model_observation({"channel": channel, "content": "fixture", "start_ms": 0, "end_ms": 1000})
    assert error.value.code == "MODEL_RESPONSE_INVALID" and not error.value.unknown_remote
    format = video_format(45000)
    assert channel not in format["schema"]["properties"]["channel"]["enum"]
    assert format["format_contract"]["local_bounds_ms"] == [0, 45000]


def test_generic_audio_is_preserved_without_becoming_speech():
    raw = {"channel": "audio", "content": "笑声与音效", "start_ms": 7800, "end_ms": 10100}
    item = model_observation(raw)
    assert item.channel == "unclassified_audio"
    assert item.content == raw["content"] and item.start_ms == 7800 and item.end_ms == 10100
    assert raw["channel"] == "audio"


def test_video_request_offers_existing_non_speech_category_without_relabeling_evidence():
    format = video_format(45000)
    assert "unclassified_audio" in format["schema"]["properties"]["channel"]["enum"]
    raw = {"channel": "unclassified_audio", "content": "撞击特效音与笑声", "start_ms": 8200, "end_ms": 9900}
    item = model_observation(raw)
    assert item.channel == "unclassified_audio"
    assert item.content == raw["content"] and item.start_ms == 8200 and item.end_ms == 9900


def test_verification_context_separates_global_time_and_local_overlap():
    observation = SimpleNamespace(
        id="e", channel="spoken_audio", content="原话", start_ms=95000, end_ms=100000
    )
    result = verification_evidence([observation], {"actual_start_ms": 92000, "actual_end_ms": 103000})
    assert result[0]["original_range_ms"] == [95000, 100000]
    assert result[0]["clip_local_overlap_ms"] == [3000, 8000]
    assert result[0]["evidence_id"] == "e" and "id" not in result[0] and "origin" not in result[0]


def test_competitor_recheck_has_primary_brand_source_facts_and_global_timeline():
    check = {"rule_id": "competitor", "status": "UNCERTAIN", "evidence_ids": ["original"]}
    evidence = SimpleNamespace(
        id="original", channel="visual_event", content="示例饮品杯", start_ms=95000, end_ms=100000
    )
    fact = {**rule_for("source", id="identity"), "type": "fact", "title": "推广产品为示例品牌"}
    product = {"brand": "示例品牌", "product": "示例饮品"}
    payload = verification_payload(
        check,
        rule_for("source", id="competitor"),
        {"original": evidence},
        {"actual_start_ms": 92000, "actual_end_ms": 103000},
        162963,
        product,
        [fact],
    )
    assert payload["product_context"] == {"brand": "示例品牌", "product": "示例饮品"}
    assert payload["frozen_source_facts"][0]["source_refs"] == fact["source_refs"]
    assert payload["timeline"] == {
        "original_duration_ms": 162963,
        "clip_global_range_ms": [92000, 103000],
        "clip_local_range_ms": [0, 11000],
    }
    assert payload["candidate_evidence"][0]["clip_local_overlap_ms"] == [3000, 8000]
    assert payload["candidate"]["evidence_ids"] == ["E1"]
    assert check["evidence_ids"] == ["original"]  # The original candidate remains intact.
    assert payload["verification_context_version"] == 3


@pytest.mark.parametrize("status", ["PASS", "FAIL"])
def test_unclassified_audio_alone_cannot_decide_a_rule(status):
    rule = rule_for("source")
    observation = SimpleNamespace(id="sound", channel="unclassified_audio")
    stored = []
    db = SimpleNamespace(scalars=lambda query: [observation], add=stored.append)
    run = SimpleNamespace(id="run", kind="video")
    result = persist_checks(
        db,
        run,
        [rule],
        {
            "checks": [
                {
                    "rule_id": rule["id"],
                    "status": status,
                    "evidence_ids": ["sound"],
                    "explanation": "Fixture only",
                    "limitations": [],
                }
            ]
        },
    )
    assert result[0]["status"] == "UNCERTAIN"
    assert run.assessment == "NEEDS_HUMAN"


@pytest.mark.parametrize("kind,expected", [("script", "PASS"), ("video", "UNCERTAIN")])
def test_planned_script_time_does_not_become_actual_video_time(kind, expected):
    rule = rule_for("source")
    rule["predicate"].update(kind="time_window", window_ms=[0, 15000])
    observation = SimpleNamespace(id="quote", start_ms=None, uncertainty_ms=None)
    stored = []
    db = SimpleNamespace(scalars=lambda query: [observation], add=stored.append)
    run = SimpleNamespace(id="run", kind=kind)
    check = {
        "rule_id": rule["id"],
        "status": "PASS",
        "evidence_ids": ["quote"],
        "explanation": "Fixture: script explicitly plans 00:05; this is not an actual video timestamp.",
        "limitations": [],
    }
    result = persist_checks(db, run, [rule], {"checks": [check]})
    assert result[0]["status"] == expected
    assert stored[0].status == expected


def test_conflict_id_repair_preserves_all_non_reference_fields():
    conflict = {
        "id": "conflict",
        "rule_ids": ["short-id", "known"],
        "description": "Original unresolved brand conflict",
        "blocking": True,
        "status": "pending",
        "resolution": "",
    }
    original = deepcopy(conflict)
    rules = [{"id": key, "title": key, "scope": {}} for key in ["complete-id", "known"]]
    calls = []

    def respond(*args, **kwargs):
        calls.append(kwargs)
        return {"references": [{"conflict_id": "conflict", "rule_ids": ["complete-id", "known"]}]}

    result, attempted = grounded_conflicts(SimpleNamespace(json=respond), [conflict], rules)
    assert attempted and len(calls) == 1 and calls[0]["thinking"] == "disabled"
    assert conflict == original
    assert {k: v for k, v in result[0].items() if k != "rule_ids"} == {
        k: v for k, v in original.items() if k != "rule_ids"
    }


def test_unique_delimited_prefix_completion_retains_original_members_without_model_call():
    conflict = {"id": "c", "rule_ids": ["known", "page-R07"], "description": "Original", "blocking": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("Unambiguous ID completion must not call a model")

    result, repaired = grounded_conflicts(
        SimpleNamespace(json=forbidden),
        [conflict],
        [{"id": "known"}, {"id": "page-R07_mandatory_tags"}],
    )
    assert repaired
    assert result[0]["rule_ids"] == ["known", "page-R07_mandatory_tags"]
    assert conflict["rule_ids"] == ["known", "page-R07"]


def test_model_cannot_drop_known_members_when_prefix_is_ambiguous():
    conflict = {"id": "c", "rule_ids": ["known", "short"], "description": "Original", "blocking": True}
    provider = SimpleNamespace(
        json=lambda *args, **kwargs: {
            "references": [{"conflict_id": "c", "rule_ids": ["short_a"]}],
        }
    )
    rules = [{"id": key, "title": key, "scope": {}} for key in ["known", "short_a", "short_b"]]
    with pytest.raises(ReviewError):
        grounded_conflicts(provider, [conflict], rules)


def test_prefix_completion_cannot_silently_merge_two_original_members():
    conflict = {
        "id": "c",
        "rule_ids": ["page-R07", "page-R07_tags"],
        "description": "Original two references",
        "blocking": True,
    }
    with pytest.raises(ReviewError):
        grounded_conflicts(None, [conflict], [{"id": "page-R07_tags"}])


@pytest.mark.parametrize(
    "references",
    [
        [],
        [{"conflict_id": "conflict", "rule_ids": []}],
        [{"conflict_id": "conflict", "rule_ids": ["unknown"]}],
        [{"conflict_id": "conflict", "rule_ids": ["known", "known"]}],
        [{"conflict_id": "conflict", "rule_ids": ["known"], "blocking": False}],
    ],
)
def test_incomplete_or_modified_conflict_reference_repairs_are_rejected(references):
    provider = SimpleNamespace(json=lambda *args, **kwargs: {"references": references})
    conflict = {"id": "conflict", "rule_ids": ["short"], "description": "Unresolved", "blocking": True}
    with pytest.raises(ReviewError):
        grounded_conflicts(provider, [conflict], [{"id": "known", "title": "Known", "scope": {}}])


def test_check_reference_completion_preserves_verdict_and_all_members():
    checks = [
        {
            "rule_id": "R",
            "status": "FAIL",
            "evidence_ids": ["bc1a12e4", "known"],
            "explanation": "Original",
            "suggestion": "Original suggestion",
            "limitations": ["Original limit"],
        }
    ]
    original = deepcopy(checks)
    result = grounded_check_references(checks, {"bc1a12e4-1111-2222", "known"})
    assert result["checks"][0]["evidence_ids"] == ["bc1a12e4-1111-2222", "known"]
    for key in ["rule_id", "status", "explanation", "suggestion", "limitations"]:
        assert result["checks"][0][key] == original[0][key]
    assert checks == original and len(result["reference_changes"]) == 1


@pytest.mark.parametrize(
    "ids,known",
    [
        (["short"], {"short-a", "short-b"}),
        (["missing"], {"known"}),
        (["short", "short-a"], {"short-a"}),
    ],
)
def test_ambiguous_unknown_or_collapsed_evidence_refs_are_rejected(ids, known):
    with pytest.raises(ReviewError):
        grounded_check_references(
            [{"rule_id": "R", "status": "PASS", "evidence_ids": ids, "explanation": "Fixture"}], known
        )


def test_verifier_can_reference_exact_existing_evidence_without_duplicate_observation():
    from app.workers.review import observation_dict

    known = SimpleNamespace(
        id="known",
        asset_id="video",
        channel="onscreen_text",
        content="原始字幕",
        start_ms=0,
        end_ms=1000,
        uncertainty_ms=None,
        source_block_id=None,
        origin={"source": "observe_full"},
    )
    raw = observation_dict(known)
    assert existing_verification_reference(raw, {"known": known}) == "known"
    assert raw == observation_dict(known)
    for changed in [
        {**raw, "content": "changed"},
        {**raw, "origin": {"source": "human"}},
        {**raw, "id": "unknown"},
    ]:
        with pytest.raises(ReviewError):
            existing_verification_reference(changed, {"known": known})
