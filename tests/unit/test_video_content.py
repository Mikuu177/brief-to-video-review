import pytest
from app.errors import ReviewError
from app.media.content_timeline import content_units
from app.services.video_content import content_report, normalized_asr, reconcile, validate_batch
from app.services.video_scope import render_limitation, scoped_limitations


def batch(namespace="A"):
    return {
        "events": [
            {
                "id": namespace + "_E1",
                "channel": "spoken_audio",
                "raw_content": "不好喝。",
                "start_ms": 0,
                "end_ms": 1000,
            }
        ],
        "audio_observed": True,
        "inspected_channels": ["spoken_audio", "visual_event"],
    }


def test_segment_cannot_promote_absence_to_whole_film():
    result = scoped_limitations(["视频全程未出现目标产品"], 120000, 162963, 162963, "inspect:last")[0]
    assert result["scope"] == "interval" and result["status"] == "SCOPE_VIOLATION"
    assert "120–162.963" in render_limitation(result)
    assert result["raw_text"] == "视频全程未出现目标产品"


def test_whole_input_observation_still_is_not_absence_proof():
    result = scoped_limitations(["未发现产品"], 0, 1000, 1000, "full")[0]
    assert result["status"] == "UNVERIFIED" and result["effect"] == "不构成全片缺失证据"


def test_actor_cannot_reference_unknown_entity():
    raw = batch()
    raw["events"][0]["actor_id"] = "imaginary"
    with pytest.raises(ReviewError, match="主体"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_claim_cannot_invent_evidence_reference():
    raw = batch()
    raw["claims"] = [
        {"id": "A_C1", "kind": "interpretation", "description": "喝后清醒", "evidence_ids": ["not-present"]}
    ]
    with pytest.raises(ReviewError, match="事实"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_rounded_endpoint_is_preserved_as_unclear():
    raw = batch()
    raw["events"][0]["end_ms"] = 1100
    result = validate_batch(raw, "A", {"actual_start_ms": 2000, "actual_end_ms": 3000}, 4000)["events"][0]
    assert result["end_ms"] == 3000 and result["status"] == "UNCLEAR"
    assert result["origin"]["reported_local_ms"] == [0, 1100]
    assert result["uncertainty_ms"] == 200


def test_out_of_clip_time_rejected_without_seconds_guessing():
    raw = batch()
    raw["events"][0]["end_ms"] = 20000
    with pytest.raises(ReviewError, match="时间"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 50000)


def test_model_cannot_create_human_confirmation():
    raw = batch()
    raw["events"][0]["status"] = "HUMAN_CONFIRMED"
    with pytest.raises(ReviewError, match="契约"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_unit_partition_preserves_tail_and_cross_boundary_speech():
    utterances = [{"start_time": 19000, "end_time": 24000, "speaker_id": "0"}]
    units = content_units(61001, [20000], [{"at_ms": 20000, "reason": "话题"}], utterances)
    assert units[0]["start_ms"] == 0 and units[-1]["end_ms"] == 61001
    assert all(a["end_ms"] == b["start_ms"] for a, b in zip(units, units[1:]))
    assert units[0]["context_end_ms"] >= 24000


def test_speaker_zero_words_and_raw_product_name_are_preserved():
    raw = {
        "utterances": [
            {
                "text": "带咖啡冰拿铁",
                "start_time": 0,
                "end_time": 1000,
                "additions": {"speaker": 0},
                "words": [{"text": "带咖啡", "start_time": 0, "end_time": 500}],
            }
        ],
        "request_id": "test",
        "usage": {},
    }
    result = normalized_asr(raw, {"offset_ms": 100, "timing_tolerance_ms": 0}, 2000)
    event = result["events"][0]
    assert event["speaker_id"] == "voice:0" and event["actor_id"] is None
    assert event["raw_content"] == "带咖啡冰拿铁" and event["origin"]["words"][0]["word"] == "带咖啡"
    assert event["start_ms"] == 100 and result["capabilities"]["with_words"] == 1


def test_no_speaker_does_not_get_synthesized():
    raw = {
        "utterances": [{"text": "hello", "start_time": 0, "end_time": 1000}],
        "request_id": "test",
        "usage": {},
    }
    result = normalized_asr(raw, {"offset_ms": 0, "timing_tolerance_ms": 0}, 1000)
    assert result["events"][0]["speaker_id"] is None and result["capabilities"]["with_speaker"] == 0


def test_completed_ranges_are_not_accuracy_or_production_approval():
    report = content_report({"duration_ms": 1000, "units": [{"start_ms": 0, "end_ms": 1000}], "events": []})
    assert (
        report["processing_complete"] is True
        and report["production_ready"] is False
        and report["accuracy"] is None
    )
    assert report["readability"] == "NEEDS_HUMAN"


def test_blind_only_event_and_zero_event_unit_are_visible():
    gaps = reconcile(
        [{"id": "B_E1", "channel": "visual_event", "start_ms": 0, "end_ms": 500, "origin": {"stage": "B"}}],
        [{"id": "U001", "start_ms": 0, "end_ms": 1000}],
    )
    assert {g["code"] for g in gaps} == {"B_ONLY_CANDIDATE", "ZERO_EVENT_UNIT"}


def test_known_protocol_echoes_are_checked_and_archived():
    raw = batch()
    raw.update(namespace="A", duration_ms=1000)
    out = validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)
    assert out["protocol_echoes"] == {"namespace": "A", "duration_ms": 1000}
    raw["duration_ms"] = 2000
    with pytest.raises(ReviewError, match="回显"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_entity_reference_expands_only_to_known_primary_events():
    raw = batch()
    raw["entities"] = [{"id": "A_P1", "kind": "object", "description": "杯子", "evidence_ids": ["A_E1"]}]
    raw["claims"] = [
        {"id": "A_C1", "kind": "interpretation", "description": "候选品牌", "evidence_ids": ["A_P1"]}
    ]
    out = validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)
    claim = out["claims"][0]
    assert claim["evidence_ids"] == ["A_E1"] and claim["reported_evidence_ids"] == ["A_P1"]
    raw["entities"][0]["evidence_ids"] = []
    with pytest.raises(ReviewError, match="原始事实"):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_encoded_tail_rounding_is_unclear_and_never_an_exact_time():
    raw = batch()
    result = validate_batch(raw, "A", {"actual_start_ms": 3000, "actual_end_ms": 4000}, 3990)["events"][0]
    assert result["end_ms"] == 3990 and result["status"] == "UNCLEAR"
    assert result["origin"]["reported_local_ms"] == [0, 1000] and result["origin"]["source_end_normalized"]


def test_sound_bridge_keeps_sound_separate_from_spoken_audio():
    from types import SimpleNamespace

    from app.services.content_binding import content_evidence

    snapshot = SimpleNamespace(
        id="snapshot",
        content_hash="hash",
        payload={
            "events": [
                {
                    "id": "S1",
                    "channel": "sound_event",
                    "raw_content": "敲击声",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "status": "CANDIDATE",
                }
            ]
        },
    )
    result = content_evidence(snapshot)
    assert (
        result[0]["channel"] == "unclassified_audio"
        and result[0]["origin"]["content_channel"] == "sound_event"
    )


def test_generic_audio_never_becomes_verified_spoken_language():
    raw = batch()
    raw["events"][0]["channel"] = "audio"
    result = validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)["events"][0]
    assert result["channel"] == "sound_event" and result["status"] == "UNCLEAR"
    assert result["origin"]["reported_channel"] == "audio" and raw["events"][0]["channel"] == "audio"
    raw["events"][0]["status"] = "HUMAN_CONFIRMED"
    with pytest.raises(ReviewError):
        validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)


def test_missing_claim_index_is_generated_without_inventing_semantics():
    raw = batch()
    raw["claims"] = [{"kind": "interpretation", "description": "待核对关系", "evidence_ids": ["A_E1"]}]
    result = validate_batch(raw, "A", {"actual_start_ms": 0, "actual_end_ms": 1000}, 1000)
    assert result["claims"][0]["id"] == "A_PARSER_C0001" and result["claims"][0]["reported_id"] is None
    assert (
        result["claims"][0]["description"] == raw["claims"][0]["description"] and "id" not in raw["claims"][0]
    )
