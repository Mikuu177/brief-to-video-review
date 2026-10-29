import json
import math
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from app.errors import ReviewError

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
FRAME = re.compile(rf"\bn:\s*(\d+)\s+pts:.*?\bpts_time:\s*({NUMBER})")
DURATION = re.compile(rf"\bduration_time:\s*({NUMBER})")


@lru_cache(maxsize=4)
def probe_frame_times(path: Path, size: int, mtime_ns: int):
    # Source assets are immutable, content-addressed files. Include stat data
    # in the cache key and keep it bounded to this worker process.
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=pts_time,best_effort_timestamp_time,duration_time,pkt_duration_time",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        raw = json.loads(result.stdout)["frames"]
        if not raw or len(raw) > 100000:
            raise ValueError("Frame inventory outside budget")
        frames = []
        for item in raw:
            pts = float(item.get("pts_time", item.get("best_effort_timestamp_time")))
            duration = float(item.get("duration_time", item.get("pkt_duration_time")))
            if not math.isfinite(pts) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("Missing or invalid explicit frame timing")
            frames.append((pts, duration))
        return tuple(frames)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise ReviewError("MEDIA_INVALID", "完整解码帧的时间元数据无法核验；未使用FPS推测持续时间") from exc


def decoded_frame_inventory(
    log: str, source_path: Path | None = None, start_ms: int | None = None, end_ms: int | None = None
):
    entries = []
    for line in log.splitlines():
        match = FRAME.search(line)
        if match:
            duration = DURATION.search(line)
            entries.append((int(match[1]), float(match[2]), float(duration[1]) if duration else None))
    if not entries or [f[0] for f in entries] != list(range(len(entries))):
        return [], "invalid_showinfo_inventory"
    if all(f[2] is not None and math.isfinite(f[2]) and f[2] > 0 for f in entries):
        return entries, "showinfo_duration_time"
    # FFmpeg 5.1 showinfo has no duration_time. Obtain explicit durations by
    # decoding all source frames, then align every frame to the actual trim log.
    # Missing metadata or a mismatch remains a failure; average FPS is not a substitute.
    if source_path is None or any(f[2] is not None for f in entries):
        return [], "missing_or_inconsistent_duration"
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise ReviewError("MEDIA_INVALID", "无法读取原片，未放行未经核验的帧时间") from exc
    source = probe_frame_times(source_path.resolve(), stat.st_size, stat.st_mtime_ns)
    selected = [
        (pts, duration)
        for pts, duration in source
        if (start_ms is None or pts >= start_ms / 1000) and (end_ms is None or pts < end_ms / 1000)
    ]
    # showinfo prints PTS with limited significant digits. 1ms accommodates
    # that rounding within the supported 5-minute input, without dropping frames.
    if len(selected) != len(entries) or any(
        abs(entry[1] - frame[0]) > 0.001 for entry, frame in zip(entries, selected)
    ):
        return [], "frame_pts_or_count_mismatch"
    return [(i, pts, duration) for i, (pts, duration) in enumerate(selected)], "ffprobe_frame_duration"
