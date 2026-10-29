import re
import shutil
import subprocess

import pytest
from app.media.content_timeline import decode_timeline
from app.media.prepare import continuous_clip, validate_clip
from app.media.probe import probe


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_real_decode_and_clip_with_legacy_showinfo(tmp_path, monkeypatch):
    source = tmp_path / "controlled-timing.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x96:rate=30:duration=1.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    original_run = subprocess.run

    def legacy_run(command, **kwargs):
        result = original_run(command, **kwargs)
        if command[0] == "ffmpeg" and result.stderr:
            result.stderr = re.sub(rb"\s+duration:\s*\S+\s+duration_time:\s*\S+", b"", result.stderr)
        return result

    monkeypatch.setattr("app.media.frame_inventory.subprocess.run", legacy_run)
    metadata = probe(source)
    timeline = decode_timeline(source, metadata, tmp_path / "timeline")
    assert timeline["frame_count"] == 36 and timeline["video_end_ms"] == 1200
    assert timeline["frame_timing_source"] == "ffprobe_frame_duration"
    assert timeline["decode_gaps"] == []
    clip = continuous_clip(source, tmp_path / "clip.mp4", 200, 900, metadata)
    assert clip["source_frame_count"] == clip["encoded_frame_count"] == 21
    assert clip["actual_start_ms"] == 200 and clip["source_video_end_ms"] == 900
    assert clip["frame_timing_source"] == "ffprobe_frame_duration"
    checked = validate_clip(source, clip, 200, 900, metadata)
    assert checked["frame_mapping_verified"] and checked["encoded_frame_count"] == 21
