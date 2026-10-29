"""Versioned derived diagnostics; never rewrite the immutable model snapshot."""

from bisect import bisect_left

from app.services.versioning import content_hash

VERSION = "word-coverage-v1"


def word_coverage_checks(snapshot):
    payload = snapshot.payload
    duration = payload.get("duration_ms", 0)
    asr = [event for event in payload.get("events", []) if event["origin"]["stage"] == "independent_asr"]
    words = sorted(
        (word["start_ms"], word["end_ms"])
        for event in asr
        for word in event["origin"].get("words", [])
        if 0 <= word["start_ms"] < word["end_ms"] <= duration
    )
    starts, maximum_ends = [], []
    for start, end in words:
        starts.append(start)
        maximum_ends.append(max(end, maximum_ends[-1] if maximum_ends else 0))
    gaps = []
    for event in payload.get("events", []) if words else []:
        if (
            event["channel"] != "spoken_audio"
            or event["origin"]["stage"] == "independent_asr"
            or event["status"] == "NOT_OBSERVED"
        ):
            continue
        margin = max(500, event.get("uncertainty_ms") or 0)
        position = bisect_left(starts, event["end_ms"] + margin) - 1
        if position < 0 or maximum_ends[position] <= event["start_ms"] - margin:
            gaps.append(
                {
                    "code": "VIDEO_SPEECH_WITHOUT_ASR_WORDS",
                    "event_id": event["id"],
                    "candidate_ids": [
                        candidate["id"]
                        for candidate in asr
                        if candidate["start_ms"] < event["end_ms"] and candidate["end_ms"] > event["start_ms"]
                    ],
                    "range_ms": [event["start_ms"], event["end_ms"]],
                    "margin_ms": margin,
                    "reason": "视频路径有口播候选，但本区间及时间容差内没有对应ASR词；可能是漏识、误听或时间偏差，需回放而非自动判定哪路正确",
                }
            )
    result = {
        "version": VERSION,
        "source_content_hash": snapshot.content_hash,
        "status": "CANDIDATES" if words else "UNAVAILABLE",
        "valid_asr_words": len(words),
        "gaps": gaps,
        "method": "word interval presence with at least 500ms margin; not semantic accuracy",
    }
    return {**result, "hash": content_hash(result)}
