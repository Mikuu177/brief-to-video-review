import json
import subprocess
from pathlib import Path

import pytest
from app.errors import ReviewError
from app.media.frame_inventory import decoded_frame_inventory
from app.media.prepare import frame_mapping


def legacy_log(times):
    return "\n".join(f"n: {i} pts: {i} pts_time:{pts:.6g} pos: 10" for i, pts in enumerate(times))


def source_with_probe(tmp_path, monkeypatch, frames):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"timing unit fixture, not playable model evidence")

    def run(command, **kwargs):
        assert command[0] == "ffprobe" and "-show_frames" in command
        assert "-read_intervals" not in command
        return subprocess.CompletedProcess(command, 0, json.dumps({"frames": frames}).encode(), b"")

    monkeypatch.setattr("app.media.frame_inventory.subprocess.run", run)
    return path


def test_legacy_duration_comes_from_every_decoded_source_frame(tmp_path, monkeypatch):
    frames = [
        {"pts_time": "119.966667", "pkt_duration_time": "0.033333"},
        {"pts_time": "120.000000", "pkt_duration_time": "0.033333"},
        {"pts_time": "120.033333", "pkt_duration_time": "0.066667"},
        {"pts_time": "120.100000", "pkt_duration_time": "0.100000"},
        {"pts_time": "120.200000", "pkt_duration_time": "0.033333"},
    ]
    source = source_with_probe(tmp_path, monkeypatch, frames)
    encoded = {"duration_ms": 200, "raw_probe": {"streams": [{"codec_type": "video", "nb_frames": "3"}]}}
    result = frame_mapping(
        legacy_log([120, 120.033333, 120.1]),
        encoded,
        120000,
        120200,
        {"duration_ms": 120200},
        Path("encoded.mp4"),
        source_path=source,
    )
    assert result["source_frame_count"] == result["encoded_frame_count"] == 3
    assert result["source_video_end_ms"] == 120200  # VFR and final hold, not 1 / average FPS.
    assert result["frame_timing_source"] == "ffprobe_frame_duration"


@pytest.mark.parametrize(
    "frames,times",
    [
        ([{"pts_time": "0", "pkt_duration_time": "0.04"}], [0, 0.04]),
        (
            [
                {"pts_time": "0.01", "pkt_duration_time": "0.04"},
                {"pts_time": "0.05", "pkt_duration_time": "0.04"},
            ],
            [0, 0.04],
        ),
    ],
)
def test_legacy_missing_or_mismatched_frames_are_not_accepted(tmp_path, monkeypatch, frames, times):
    source = source_with_probe(tmp_path, monkeypatch, frames)
    inventory, _ = decoded_frame_inventory(legacy_log(times), source)
    assert inventory == []


@pytest.mark.parametrize("duration", [None, "0", "NaN", "-0.04"])
def test_last_frame_duration_is_never_guessed(tmp_path, monkeypatch, duration):
    frame = {"pts_time": "0"}
    if duration is not None:
        frame["pkt_duration_time"] = duration
    source = source_with_probe(tmp_path, monkeypatch, [frame])
    with pytest.raises(ReviewError, match="未使用FPS推测"):
        decoded_frame_inventory(legacy_log([0]), source)


def test_duplicate_or_missing_decoded_indices_fail_before_probe():
    frames, _ = decoded_frame_inventory("n:0 pts:0 pts_time:0\nn:0 pts:1 pts_time:0.04")
    assert frames == []


def test_modern_showinfo_keeps_existing_path_without_probe(monkeypatch):
    monkeypatch.setattr(
        "app.media.frame_inventory.subprocess.run",
        lambda *args, **kwargs: pytest.fail("Modern complete showinfo must not invoke ffprobe"),
    )
    frames, method = decoded_frame_inventory("n:0 pts:0 pts_time:0 duration_time:0.04")
    assert frames == [(0, 0.0, 0.04)] and method == "showinfo_duration_time"
