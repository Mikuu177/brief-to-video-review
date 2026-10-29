import json
import re
import subprocess
from pathlib import Path

from app.errors import ReviewError
from app.media.frame_inventory import decoded_frame_inventory
from app.services.versioning import content_hash


def decode_timeline(path: Path, metadata, directory: Path):
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            "showinfo,scdet=threshold=10:sc_pass=0",
            "-an",
            "-fps_mode",
            "passthrough",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=300,
    )
    if result.returncode:
        raise ReviewError("CONTENT_DECODE_FAILED", "完整视频解码/转场测量失败")
    log = result.stderr.decode(errors="replace")
    frames, timing_source = decoded_frame_inventory(log, path)
    stream = next(s for s in metadata["raw_probe"]["streams"] if s["codec_type"] == "video")
    if (
        not frames
        or frames[-1][0] != len(frames) - 1
        or (stream.get("nb_frames") and int(stream["nb_frames"]) != len(frames))
    ):
        raise ReviewError("CONTENT_FRAME_INVENTORY", "解码帧计数与媒体头不能对齐")
    if len(frames) > 100000:
        raise ReviewError("CONTENT_CAPACITY", "解码帧数超过本轮预算")
    cuts = [round(float(x) * 1000) for x in re.findall(r"lavfi.scd.time:\s*([\d.]+)", log)]
    gaps = [
        [round((a[1] + a[2]) * 1000), round(b[1] * 1000)]
        for a, b in zip(frames, frames[1:])
        if b[1] - (a[1] + a[2]) > 0.05
    ]
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"frames": frames, "scene_cut_ms": cuts, "frame_timing_source": timing_source}
    (directory / "decoded-timeline.json").write_text(json.dumps(payload))
    return {
        "frame_count": len(frames),
        "video_start_ms": round(frames[0][1] * 1000),
        "video_end_ms": round((frames[-1][1] + frames[-1][2]) * 1000),
        "scene_cut_ms": cuts,
        "decode_gaps": gaps,
        "inventory_hash": content_hash(payload),
        "method": "all_decoded_pts_scdet_no_image_export",
        "frame_timing_source": timing_source,
    }


def content_units(duration, scene_cuts, boundaries, utterances, max_ms=30000):
    candidates = [(int(t), "scene") for t in scene_cuts]
    candidates += [(b["at_ms"], "semantic:" + b["reason"]) for b in boundaries]
    for a, b in zip(utterances, utterances[1:]):
        if b["start_time"] - a["end_time"] >= 250 or a.get("speaker_id") != b.get("speaker_id"):
            candidates.append((a["end_time"], "speech_turn_or_pause"))
    result = []
    start = 0
    while start < duration:
        eligible = [
            (t, r) for t, r in candidates if start + 4000 <= t <= min(start + max_ms, duration - 1000)
        ]
        if duration - start <= max_ms:
            end, reason = duration, "end"
        elif eligible:
            end, reason = min(
                eligible,
                key=lambda x: abs(x[0] - (start + 20000)) - (2000 if x[1].startswith("semantic:") else 0),
            )
        else:
            end, reason = start + max_ms, "budget_boundary_needs_context"
        left, right = max(0, start - 2000), min(duration, end + 2000)
        for utterance in utterances:
            if utterance["start_time"] < start < utterance["end_time"]:
                left = min(left, utterance["start_time"])
            if utterance["start_time"] < end < utterance["end_time"]:
                right = max(right, utterance["end_time"])
        result.append(
            {
                "id": f"U{len(result) + 1:03}",
                "start_ms": start,
                "end_ms": end,
                "context_start_ms": max(0, left),
                "context_end_ms": min(duration, right),
                "boundary_reason": reason,
            }
        )
        start = end
    return result
