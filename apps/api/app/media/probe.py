import json
import subprocess
from pathlib import Path

from app.errors import ReviewError


def probe(path: Path) -> dict:
    try:
        process = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            check=True,
            timeout=30,
        )
        data = json.loads(process.stdout)
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
        start_time = float(video.get("start_time", 0))
        return {
            "duration_ms": round(float(data["format"]["duration"]) * 1000),
            "width": video["width"],
            "height": video["height"],
            "codec": video["codec_name"],
            "fps": video.get("avg_frame_rate"),
            "video_start_ms": round(start_time * 1000),
            "audio_start_ms": round(float(audio.get("start_time", 0)) * 1000) if audio else None,
            "has_audio": audio is not None,
            "raw_probe": data,
        }
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, OSError) as exc:
        raise ReviewError("MEDIA_INVALID", "视频信息读取失败或没有视频轨道") from exc
