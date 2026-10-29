import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from app.errors import ReviewError
from app.services.storage import external_audio_url
from app.settings import settings

BASE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"
STANDARD_RESOURCES = {"volc.seedasr.auc", "volc.bigasr.auc"}


def transcribe(
    path: Path, trace_dir: Path, resume_request_id: str | None = None, *, detailed: bool = False
) -> dict:
    resource = settings.asr_resource_id
    if resource not in STANDARD_RESOURCES | {"volc.bigasr.auc_turbo"}:
        raise ReviewError("ASR_RESOURCE_UNSUPPORTED", "ASR 资源未适配，未上传或提交识别")
    trace_dir.mkdir(parents=True, exist_ok=True)
    audio_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = None
    for candidate in sorted(trace_dir.glob("asr-*-task.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        saved = json.loads(candidate.read_text())
        explicit = resume_request_id == saved["request_id"]
        if (
            saved.get("resource_id") == resource
            and saved.get("options_hash", "legacy") == ("speaker-words-v1" if detailed else "legacy")
            and (saved.get("audio_sha256") == audio_sha or (explicit and not saved.get("audio_sha256")))
            and (not resume_request_id or explicit)
            and saved.get("status") in ("SUBMITTING", "QUERYING", "UNKNOWN_REMOTE", "COMPLETED")
        ):
            previous = saved
            break
    if resume_request_id and previous is None:
        raise ReviewError("ASR_RESUME_MISMATCH", "未找到同音轨/资源的可恢复ASR任务，未重新提交")
    request_id = previous["request_id"] if previous else str(uuid4())
    if previous and resource not in STANDARD_RESOURCES and previous["status"] != "COMPLETED":
        raise ReviewError(
            "ASR_RESUME_UNSUPPORTED", "极速ASR远端状态不明，不能自动重新提交", unknown_remote=True
        )
    task_path = trace_dir / f"asr-{request_id}-task.json"
    task = previous or {
        "request_id": request_id,
        "resource_id": resource,
        "status": "PREPARING",
        "options_hash": "speaker-words-v1" if detailed else "legacy",
        "responses": [],
    }
    task["audio_sha256"] = audio_sha
    if previous:
        task.setdefault("recovery_events", []).append({"previous_status": task["status"], "at": time.time()})
    task_path.write_text(json.dumps(task, ensure_ascii=False))
    url = None if previous else external_audio_url(path)
    headers = {
        "X-Api-Key": settings.asr_api_key,
        "X-Api-Resource-Id": resource,
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
    }
    payload = {
        "user": {"uid": "creator-review"},
        "audio": {"url": url, "format": "wav"},
        "request": {
            "model_name": "bigmodel",
            "show_utterances": True,
            "enable_ddc": False,
            "enable_itn": False,
        },
    }
    if detailed:
        payload["request"]["enable_speaker_info"] = True
    deadline = time.monotonic() + settings.provider_timeout_seconds

    def post(endpoint: str, body: dict):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            task["status"] = "UNKNOWN_REMOTE"
            task_path.write_text(json.dumps(task, ensure_ascii=False))
            raise ReviewError("PROVIDER_TIMEOUT", "独立 ASR 等待超时，任务 ID 已记录", unknown_remote=True)
        task["status"] = "QUERYING" if endpoint == "/query" else "SUBMITTING"
        task_path.write_text(json.dumps(task, ensure_ascii=False))
        try:
            if settings.asr_local_address:
                with httpx.Client(
                    timeout=remaining,
                    transport=httpx.HTTPTransport(local_address=settings.asr_local_address),
                    trust_env=False,
                ) as client:
                    response = client.post(BASE_URL + endpoint, headers=headers, json=body)
            else:
                response = httpx.post(BASE_URL + endpoint, headers=headers, json=body, timeout=remaining)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            task["status"] = "UNKNOWN_REMOTE"
            task_path.write_text(json.dumps(task, ensure_ascii=False))
            code = (
                "PROVIDER_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "PROVIDER_CONNECTION_ERROR"
            )
            raise ReviewError(
                code, "独立 ASR 连接失败，任务 ID 已记录，远端状态不明", unknown_remote=True
            ) from exc
        task["responses"].append(
            {
                "operation": endpoint,
                "http_status": response.status_code,
                "api_status": response.headers.get("X-Api-Status-Code"),
                "log_id": response.headers.get("X-Tt-Logid"),
            }
        )
        task_path.write_text(json.dumps(task, ensure_ascii=False))
        # Failed bodies may echo URLs; keep them only in the private raw trace directory.
        (trace_dir / f"asr-{request_id}-response-{len(task['responses'])}.json").write_text(
            json.dumps({"body": response.text}, ensure_ascii=False)
        )
        return response

    def failed():
        task["status"] = "FAILED"
        task_path.write_text(json.dumps(task, ensure_ascii=False))
        raise ReviewError("ASR_FAILED", "独立 ASR 返回失败，请检查服务权限及录音资源配置")

    result_path = trace_dir / f"asr-{request_id}.json"
    result = None
    if previous and task["status"] == "COMPLETED" and result_path.exists():
        try:
            result = json.loads(result_path.read_text())
        except ValueError as exc:
            raise ReviewError("MODEL_RESPONSE_INVALID", "已完成ASR缓存无法读取，未重新提交") from exc
    elif resource in STANDARD_RESOURCES:
        if not previous:
            response = post("/submit", payload)
            if response.status_code != 200 or response.headers.get("X-Api-Status-Code") != "20000000":
                failed()
        log_ids = [r["log_id"] for r in task["responses"] if r.get("log_id")]
        if log_ids:
            headers["X-Tt-Logid"] = log_ids[-1]
        while True:
            time.sleep(min(5, max(0, deadline - time.monotonic())))
            response = post("/query", {})
            status = response.headers.get("X-Api-Status-Code")
            if response.status_code != 200 or status not in ("20000000", "20000001", "20000002"):
                failed()
            if status == "20000000":
                break
    else:
        if previous:
            raise ReviewError("ASR_RESUME_UNSUPPORTED", "极速ASR完成响应缺失，未重新提交")
        response = post("/recognize/flash", payload)
        if response.status_code != 200 or response.headers.get("X-Api-Status-Code") != "20000000":
            failed()
    try:
        if result is None:
            result = response.json()
        utterances = result.get("result", {}).get("utterances")
        if not isinstance(utterances, list):
            raise ValueError("Missing utterance list")
    except (ValueError, AttributeError) as exc:
        task["status"] = "INVALID_RESPONSE"
        task_path.write_text(json.dumps(task, ensure_ascii=False))
        raise ReviewError("MODEL_RESPONSE_INVALID", "ASR 未返回所需的分句时间，不能作为完整转写") from exc
    task["status"] = "COMPLETED"
    task_path.write_text(json.dumps(task, ensure_ascii=False))
    (trace_dir / f"asr-{request_id}.json").write_text(json.dumps(result, ensure_ascii=False))
    return {
        "utterances": utterances,
        "request_id": request_id,
        "source": "independent_asr",
        "resource_id": resource,
        "usage": result.get("usage", {}),
        "reused_task": previous is not None,
    }
