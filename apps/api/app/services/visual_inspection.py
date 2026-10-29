"""Evidence-led visual diagnostics, independent of brand approval."""

import subprocess
from typing import Literal

from app.errors import ReviewError
from app.media.prepare import continuous_clip, probe
from app.models import Asset
from app.services.content_binding import bound_content
from app.services.runs import prompt, stage
from app.services.storage import ensure_local, local_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError

METHOD_VERSION = "visual-inspection-v3"

CATEGORIES = {
    "product_visibility",
    "text_readability",
    "product_appearance",
    "background_distraction",
    "effect_interference",
}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "product_visibility",
        "text_readability",
        "product_appearance",
        "background_distraction",
        "effect_interference",
    ]
    status: Literal["ISSUE", "OK", "UNCERTAIN", "NOT_APPLICABLE"]
    title: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1500)
    suggestion: str = Field(default="", max_length=1500)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)


class Inspection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding] = Field(min_length=5, max_length=25)
    limitations: list[str] = Field(max_length=20)


def validate_inspection(raw, duration):
    try:
        result = Inspection.model_validate(raw).model_dump()
    except ValidationError as exc:
        raise ReviewError("VISUAL_RESPONSE_INVALID", "专项结果结构无效，原响应保留") from exc
    if {f["category"] for f in result["findings"]} != CATEGORIES:
        raise ReviewError("VISUAL_RESPONSE_INVALID", "五类检查未完整返回")
    for f in result["findings"]:
        a, b = f["start_ms"], f["end_ms"]
        if (a is None) != (b is None) or (a is not None and not 0 <= a < b <= duration):
            raise ReviewError("VISUAL_TIME_INVALID", "专项证据时间超出原片或不完整")
        if f["status"] == "ISSUE" and a is None:
            raise ReviewError("VISUAL_EVIDENCE_MISSING", "疑似问题必须给出可回放区间")
    return result


def inspect_visuals(db, run, provider):
    source = run.input_snapshot
    asset = db.get(Asset, source["asset_id"])
    content = bound_content(db, source["content_snapshot_id"], asset, source["content_hash"])

    duration = content.payload["duration_ms"]
    context = {
        "target": source["target"],
        "applicable_video_rules": source["video_rules"],
        "duration_ms": duration,
        "duration_seconds": duration / 1000,
        "schema": Inspection.model_json_schema(),
        "reference_images": [],
        "asr_timestamp_candidates": [
            {"start_ms": e["start_ms"], "end_ms": e["end_ms"], "text": e["raw_content"]}
            for e in content.payload["events"]
            if e.get("origin", {}).get("stage") == "independent_asr"
        ],
    }

    def read(instruction, extra):
        feedback = {}
        for attempt in range(2):
            raw = provider.json(
                prompt(instruction),
                {**context, **extra, "validation_feedback": feedback},
                video=ensure_local(asset.storage_key),
                fps=5,
                thinking="disabled",
            )
            try:
                result = validate_inspection(raw, duration)
            except ReviewError as exc:
                if attempt:
                    raise
                feedback = {
                    "code": exc.code,
                    "message": str(exc),
                    "previous_response": raw,
                    "instruction": "重新完整观看并输出五类完整JSON；ISSUE必须有起止毫秒区间，无法核实则UNCERTAIN且时间null，不得猜时间",
                }
                continue
            return {**result, "_provider_records": provider.take_records()}
        raise ReviewError("VISUAL_RESPONSE_INVALID", "专项结果无法修复")

    candidate = stage(db, run, "visual:full_video", lambda: read("visual_inspection", {}))
    result = stage(
        db,
        run,
        "visual:independent_full_recheck",
        lambda: read("visual_verify", {"candidate": candidate["findings"]}),
    )
    findings = []
    for index, finding in enumerate(result["findings"]):
        item = dict(finding)
        if item["status"] == "ISSUE":

            def verify(item=item, index=index):
                start = max(0, item["start_ms"] - 1500)
                end = min(duration, item["end_ms"] + 1500)
                clip = local_path(f"runs/{run.id}/visual-clips/{index}.mp4")
                clip.parent.mkdir(parents=True, exist_ok=True)
                metadata = probe(ensure_local(asset.storage_key))
                mapping = continuous_clip(ensure_local(asset.storage_key), clip, start, end, metadata)
                # This verification checks visual evidence only; full reads retain original audio.
                visual_clip = clip.with_name(f"{index}-visual.mp4")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-i",
                        mapping["path"],
                        "-map",
                        "0:v:0",
                        "-c:v",
                        "copy",
                        "-an",
                        "-movflags",
                        "+faststart",
                        "-y",
                        str(visual_clip),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=180,
                )
                start = mapping["actual_start_ms"]
                raw = provider.json(
                    prompt("visual_clip_verify"),
                    {
                        "finding": item,
                        "audio_included": False,
                        "clip_start_ms": start,
                        "finding_interval_relative_ms": [item["start_ms"] - start, item["end_ms"] - start],
                    },
                    video=visual_clip,
                    fps=5,
                    thinking="disabled",
                )
                if (
                    raw.get("support") not in ("supports", "contradicts", "uncertain")
                    or not isinstance(raw.get("observation"), str)
                    or not raw["observation"].strip()
                ):
                    raise ReviewError("VISUAL_RESPONSE_INVALID", "连续区间复核返回无效")
                return {
                    **raw,
                    "clip_mapping": mapping,
                    "audio_included": False,
                    "_provider_records": provider.take_records(),
                }

            check = stage(db, run, f"visual:clip_recheck:{index}", verify)
            item["interval_recheck"] = {"support": check["support"], "observation": check["observation"]}
            if check["support"] != "supports":
                item["status"] = "UNCERTAIN"
                item["reason"] = (
                    "原判断未获连续区间复核支持：" + check["observation"] + "。原候选：" + item["reason"]
                )
        findings.append(item)
    run.assessment = "NEEDS_HUMAN"
    return {
        **result,
        "findings": findings,
        "method_version": METHOD_VERSION,
        "content_snapshot_id": content.id,
        "content_hash": content.content_hash,
        "source_sha256": asset.sha256,
        "production_ready": False,
        "approval": False,
        "limits": [
            "两轮完整MP4及疑似问题连续区间复核；平台按5fps预处理，瞬时信息可能遗漏",
            "模型复核仍是候选证据，不是独立人工金标准",
            "没有已核验的品牌参考图或标注样例，审美与颜色不能据此自动批准",
        ],
    }
