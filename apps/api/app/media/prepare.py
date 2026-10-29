import subprocess
import wave
from pathlib import Path

from app.errors import ReviewError
from app.media.frame_inventory import decoded_frame_inventory
from app.media.probe import probe


def frame_mapping(
    log: str,
    encoded: dict,
    start_ms: int,
    end_ms: int,
    metadata: dict,
    path: Path,
    *,
    source_path: Path | None = None,
):
    frames, timing_source = decoded_frame_inventory(log, source_path, start_ms, end_ms)
    if not frames:
        raise ReviewError("MEDIA_INVALID", "无法验证源片段全部帧的时间及持续长度")
    video = next(s for s in encoded["raw_probe"]["streams"] if s["codec_type"] == "video")
    if int(video.get("nb_frames", 0)) != len(frames):
        raise ReviewError("MEDIA_INVALID", "连续片段输出帧数与解码源帧数不一致")
    actual_start = round(frames[0][1] * 1000)
    source_video_end = round((frames[-1][1] + frames[-1][2]) * 1000)
    actual_end = max(source_video_end, actual_start + encoded["duration_ms"])
    return {
        "path": str(path),
        "requested_start_ms": start_ms,
        "requested_end_ms": end_ms,
        "actual_start_ms": actual_start,
        "actual_end_ms": actual_end,
        "encoded_duration_ms": encoded["duration_ms"],
        "source_video_end_ms": source_video_end,
        "source_frame_count": len(frames),
        "encoded_frame_count": int(video["nb_frames"]),
        "frame_mapping_verified": True,
        "frame_timing_source": timing_source,
        "tolerance_ms": max(0, actual_end - metadata["duration_ms"]),
    }


def validate_clip(path: Path, clip: dict, start_ms: int, end_ms: int, metadata: dict):
    if Path(clip["path"]) == path:
        return {**clip, "frame_mapping_verified": True, "mapping_method": "unchanged_original"}
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            f"trim=start={start_ms / 1000}:end={end_ms / 1000},showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=180,
        check=True,
    )
    result_mapping = frame_mapping(
        result.stderr.decode(errors="replace"),
        probe(Path(clip["path"])),
        start_ms,
        end_ms,
        metadata,
        Path(clip["path"]),
        source_path=path,
    )
    return {
        **result_mapping,
        "previous_mapping": clip,
        "mapping_method": "decoded_source_frames_and_encoded_count",
    }


def extract_audio(path: Path, target: Path, metadata: dict) -> dict:
    if not metadata["has_audio"]:
        return {"status": "NO_AUDIO", "path": None, "offset_ms": None}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ReviewError("AUDIO_DECODE_FAILED", "音轨存在，但完整音频解码失败") from exc
    with wave.open(str(target), "rb") as audio:
        duration_ms = round(audio.getnframes() / audio.getframerate() * 1000)
    offset = metadata["audio_start_ms"]
    return {
        "status": "READY",
        "path": str(target),
        "offset_ms": offset,
        "decoded_duration_ms": duration_ms,
        "timing_tolerance_ms": max(0, offset + duration_ms - metadata["duration_ms"]),
    }


def continuous_clip(path: Path, target: Path, start_ms: int, end_ms: int, metadata: dict) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if start_ms == 0 and end_ms >= metadata["duration_ms"]:
        return {
            "path": str(path),
            "requested_start_ms": 0,
            "requested_end_ms": end_ms,
            "actual_start_ms": 0,
            "actual_end_ms": metadata["duration_ms"],
            "tolerance_ms": 0,
            "frame_mapping_verified": True,
        }
    start, end = start_ms / 1000, end_ms / 1000
    vf = f"trim=start={start}:end={end},showinfo,setpts=PTS-STARTPTS"
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-fps_mode",
        "passthrough",
    ]
    if metadata["has_audio"]:
        command += [
            "-af",
            f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    command += ["-movflags", "+faststart", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=180, check=True)
        log = result.stderr.decode(errors="replace")
        return frame_mapping(log, probe(target), start_ms, end_ms, metadata, target, source_path=path)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ReviewError("MEDIA_DECODE_FAILED", "连续视频片段解码失败") from exc
