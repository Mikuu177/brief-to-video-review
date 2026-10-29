import copy

import pytest
from app.errors import ReviewError
from app.services.video_content import validate_batch


def data():
    return {
        "events": [
            {
                "id": "AF_E1",
                "channel": "visual_event",
                "raw_content": "人物拿杯",
                "start_ms": 0,
                "end_ms": 900,
                "actor_id": "AF_P1",
            }
        ],
        "entities": [
            {
                "id": "AF_P1",
                "kind": "person",
                "description": "匿名人物",
                "evidence_ids": ["AF_E1"],
                "status": "CANDIDATE",
            }
        ],
        "claims": [
            {"id": "S_C1", "kind": "interpretation", "description": "关系候选", "evidence_ids": ["AF_E1"]}
        ],
        "audio_observed": True,
        "inspected_channels": ["visual_event"],
    }


def validate(raw, events=None, entities=None):
    return validate_batch(raw, "S", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000, events, entities)


def test_copied_input_ids_preserve_both_candidates_and_ambiguous_roles():
    raw = data()
    raw["events"][0]["channel"] = "audio"
    unchanged = copy.deepcopy(raw)
    result = validate(raw, ["AF_E1"], {"AF_P1": ["AF_E1"]})
    event = result["events"][0]
    assert event["id"] == "S_PARSER_E0001" and event["actor_id"] is None and event["status"] == "UNCLEAR"
    assert result["claims"][0]["evidence_ids"] == ["AF_E1", "S_PARSER_E0001"]
    assert result["claims"][0]["status"] == "UNKNOWN" and result["entities"][0]["status"] == "UNKNOWN"
    assert event["origin"]["identifier_normalization"]["reported_id"] == "AF_E1" and raw == unchanged
    assert event["origin"]["audio_classification"] == "UNKNOWN"
    raw["events"][0]["status"] = "HUMAN_CONFIRMED"
    with pytest.raises(ReviewError):
        validate(raw, ["AF_E1"], {"AF_P1": ["AF_E1"]})


def test_local_identifier_labels_are_parser_namespaced_without_semantic_changes():
    result = validate(data())
    assert result["events"][0]["actor_id"] == "S_PARSER_P0001"
    assert result["entities"][0]["evidence_ids"] == ["S_PARSER_E0001"]
    assert result["events"][0]["raw_content"] == "人物拿杯"


def test_duplicate_transport_ids_still_rejected():
    raw = data()
    raw["events"].append(copy.deepcopy(raw["events"][0]))
    with pytest.raises(ReviewError, match="重复"):
        validate(raw)


def test_namespace_conversion_cannot_create_missing_evidence():
    raw = data()
    raw["claims"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ReviewError, match="事实"):
        validate(raw)


def test_single_trailing_comma_matches_only_a_registered_entity_and_keeps_provenance():
    raw = data()
    raw["events"][0]["actor_id"] = "AF_P1,"
    unchanged = copy.deepcopy(raw)
    result = validate(raw)
    event = result["events"][0]
    assert event["actor_id"] == "S_PARSER_P0001" and event["status"] == "UNCLEAR"
    change = event["origin"]["identifier_normalization"]["reference_candidates"][0]
    assert change["reported"] == "AF_P1," and change["normalization"] == "single_trailing_comma"
    assert event["raw_content"] == raw["events"][0]["raw_content"] and raw == unchanged
    assert len(result["events"]) == len(raw["events"]) and len(result["entities"]) == len(raw["entities"])


def test_trailing_comma_cannot_create_an_unregistered_entity():
    raw = data()
    raw["events"][0]["actor_id"] = "missing,"
    with pytest.raises(ReviewError, match="实体记录"):
        validate(raw)


def test_exact_registered_identifier_with_comma_takes_priority():
    raw = data()
    raw["entities"][0]["id"] = raw["events"][0]["actor_id"] = "AF_P1,"
    result = validate(raw)
    event = result["events"][0]
    change = event["origin"]["identifier_normalization"]["reference_candidates"][0]
    assert event["actor_id"] == "S_PARSER_P0001" and "normalization" not in change


def test_trailing_separator_preserves_input_and_output_identity_ambiguity():
    raw = data()
    raw["events"][0]["actor_id"] = "AF_P1,"
    event = validate(raw, ["AF_E1"], {"AF_P1": ["AF_E1"]})["events"][0]
    assert event["actor_id"] is None and event["status"] == "UNCLEAR"
    change = event["origin"]["identifier_normalization"]["reference_candidates"][0]
    assert change["candidates"] == ["AF_P1", "S_PARSER_P0001"]
