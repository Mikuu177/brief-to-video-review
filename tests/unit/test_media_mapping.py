from pathlib import Path

import pytest
from app.errors import ReviewError
from app.media.prepare import frame_mapping


def tail_log():
    return "\n".join(
        f"n: {i} pts:0 pts_time:{120 + i / 27:.9f} duration:512 duration_time:0.037037037"
        for i in range(1160)
    )


def test_source_frames_cover_tail_even_when_container_end_omits_last_hold():
    encoded = {"duration_ms": 42934, "raw_probe": {"streams": [{"codec_type": "video", "nb_frames": "1160"}]}}
    result = frame_mapping(tail_log(), encoded, 120000, 162963, {"duration_ms": 162963}, Path("tail.mp4"))
    assert result["actual_start_ms"] == 120000 and result["actual_end_ms"] == 162963
    assert result["encoded_duration_ms"] == 42934
    assert result["source_frame_count"] == result["encoded_frame_count"] == 1160
    assert result["frame_mapping_verified"]


def test_dropped_output_frame_cannot_be_called_complete():
    encoded = {"duration_ms": 42934, "raw_probe": {"streams": [{"codec_type": "video", "nb_frames": "1159"}]}}
    with pytest.raises(ReviewError, match="输出帧数"):
        frame_mapping(tail_log(), encoded, 120000, 162963, {"duration_ms": 162963}, Path("tail.mp4"))


def test_missing_source_frame_duration_is_not_guessed():
    encoded = {"duration_ms": 1000, "raw_probe": {"streams": [{"codec_type": "video", "nb_frames": "1"}]}}
    with pytest.raises(ReviewError, match="无法验证"):
        frame_mapping("n:0 pts:0 pts_time:0", encoded, 0, 1000, {"duration_ms": 1000}, Path("tail.mp4"))
