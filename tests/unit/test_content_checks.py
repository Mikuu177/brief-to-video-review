from types import SimpleNamespace

from app.services.content_checks import word_coverage_checks


def snapshot(words):
    return SimpleNamespace(
        content_hash="original-hash",
        payload={
            "duration_ms": 4000,
            "events": [
                {
                    "id": "ASR0",
                    "channel": "spoken_audio",
                    "raw_content": "前一句后一句",
                    "start_ms": 0,
                    "end_ms": 3500,
                    "status": "CANDIDATE",
                    "origin": {"stage": "independent_asr", "words": words},
                },
                {
                    "id": "G1",
                    "channel": "spoken_audio",
                    "raw_content": "漏掉的一句",
                    "start_ms": 1200,
                    "end_ms": 1800,
                    "status": "CANDIDATE",
                    "uncertainty_ms": None,
                    "origin": {"stage": "G"},
                },
            ],
        },
    )


def test_long_utterance_overlap_does_not_hide_missing_word_interval():
    source = snapshot([{"start_ms": 0, "end_ms": 400}, {"start_ms": 2800, "end_ms": 3400}])
    before = repr(source.payload)
    result = word_coverage_checks(source)
    assert result["gaps"][0]["event_id"] == "G1" and result["gaps"][0]["candidate_ids"] == ["ASR0"]
    assert result["source_content_hash"] == "original-hash" and repr(source.payload) == before


def test_word_time_presence_is_not_declared_semantic_agreement():
    result = word_coverage_checks(snapshot([{"start_ms": 1200, "end_ms": 1800}]))
    assert (
        result["status"] == "CANDIDATES"
        and not result["gaps"]
        and "not semantic accuracy" in result["method"]
    )


def test_missing_word_capability_is_unavailable_not_confirmed_no_speech():
    result = word_coverage_checks(snapshot([]))
    assert result["status"] == "UNAVAILABLE" and result["gaps"] == []
