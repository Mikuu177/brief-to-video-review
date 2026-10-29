import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from app.errors import ReviewError
from app.settings import settings

MAX_COMPLETION_TOKENS = 32768


class Ark:
    def __init__(self, trace_dir: Path):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        # Optional source binding affects only this provider's client; it does
        # not alter system routes, other providers, or TLS verification.
        if settings.ark_local_address:
            self.client = httpx.Client(
                timeout=settings.provider_timeout_seconds,
                transport=httpx.HTTPTransport(local_address=settings.ark_local_address),
                trust_env=False,
            )
        else:
            self.client = httpx.Client(timeout=settings.provider_timeout_seconds)
        self.records = []
        self.video_files = {}

    def request(self, method, endpoint, **kwargs):
        record_id = str(uuid4())
        started = time.monotonic()
        for attempt in range(1, 4):
            try:
                response = self.client.request(
                    method,
                    settings.ark_base_url + endpoint,
                    headers={"Authorization": f"Bearer {settings.ark_api_key}"},
                    **kwargs,
                )
            except httpx.TimeoutException as exc:
                raise ReviewError(
                    "PROVIDER_TIMEOUT", "供应商响应超时；远端状态不明，未自动重复提交", unknown_remote=True
                ) from exc
            except httpx.TransportError as exc:
                raise ReviewError(
                    "PROVIDER_CONNECTION_ERROR", "供应商连接中断；远端状态不明", unknown_remote=True
                ) from exc
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 3:
                time.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                # Do not expose upstream response bodies that may echo signed URLs or prompts.
                try:
                    upstream_error = response.json().get("error", {})
                except (ValueError, AttributeError):
                    upstream_error = {}
                if (
                    response.status_code == 403
                    and isinstance(upstream_error, dict)
                    and upstream_error.get("code") == "AccountOverdueError"
                ):
                    raise ReviewError(
                        "PROVIDER_ACCOUNT_OVERDUE",
                        "方舟账户欠费，供应商已拒绝请求；请在控制台处理计费状态后重试",
                    )
                raise ReviewError(
                    "PROVIDER_HTTP_ERROR", f"供应商 HTTP {response.status_code}；请检查服务端配置"
                )
            data = response.json()
            (self.trace_dir / f"{record_id}.json").write_text(json.dumps(data, ensure_ascii=False))
            self.records.append(
                {
                    "trace_id": record_id,
                    "endpoint": endpoint,
                    "attempt": attempt,
                    "request_id": data.get("id"),
                    "usage": data.get("usage", {}),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            return data

    def upload_video(self, path: Path, fps: float) -> str:
        key = (hashlib.sha256(path.read_bytes()).hexdigest(), settings.ark_preprocess_model, fps)
        if key in self.video_files:
            result = self.request("GET", f"/files/{self.video_files[key]}")
            if result.get("status") == "active":
                return result["id"]
            if result.get("status") not in ("expired", "error", "failed"):
                raise ReviewError("PROVIDER_FILE_FAILED", "已上传视频尚未就绪，请核对文件状态")
        with path.open("rb") as stream:
            result = self.request(
                "POST",
                "/files",
                files={"file": (path.name, stream, "video/mp4")},
                data={
                    "purpose": "user_data",
                    "preprocess_configs[video][model]": settings.ark_preprocess_model,
                    "preprocess_configs[video][fps]": str(fps),
                },
            )
        file_id = result["id"]
        deadline = time.monotonic() + settings.provider_timeout_seconds
        while result.get("status") not in ("active", "error", "failed", "expired"):
            if time.monotonic() > deadline:
                raise ReviewError(
                    "PROVIDER_TIMEOUT", "视频预处理超时，文件 ID 已记录，需核对后重试", unknown_remote=True
                )
            time.sleep(2)
            result = self.request("GET", f"/files/{file_id}")
        if result["status"] != "active":
            raise ReviewError("PROVIDER_FILE_FAILED", "供应商视频预处理失败")
        self.video_files[key] = file_id
        return file_id

    def json(
        self,
        instruction: str,
        payload: dict,
        video: Path | None = None,
        fps: float | None = None,
        image_data: bytes | None = None,
        thinking: Literal["enabled", "disabled"] | None = None,
    ) -> dict:
        model = settings.ark_video_model if video else settings.ark_text_model
        content = []
        if video:
            file_id = self.upload_video(video, fps or settings.review_full_fps)
            content.append({"type": "video_url", "video_url": {"file_id": file_id}})
        if image_data:
            encoded = base64.b64encode(image_data).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        text = json.dumps(payload, ensure_ascii=False)
        content.append({"type": "text", "text": text})
        (self.trace_dir / f"input-{uuid4()}.json").write_text(
            json.dumps(
                {
                    "model": model,
                    "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
                    "payload": payload,
                    "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest() if video else None,
                    "fps": fps,
                    "has_image": image_data is not None,
                    "thinking": thinking,
                    "max_completion_tokens": MAX_COMPLETION_TOKENS,
                },
                ensure_ascii=False,
            )
        )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            # Includes reasoning and answer; replaces the provider's implicit
            # 4096-answer-token default without disabling semantic reasoning.
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
        }
        if thinking is not None:
            body["thinking"] = {"type": thinking}
        result = self.request("POST", "/chat/completions", json=body)
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ReviewError(
                    "MODEL_OUTPUT_TRUNCATED",
                    "模型输出达到长度限制，完整响应已记录但JSON不予采用；请核对输出预算后重试",
                )
            if choice.get("finish_reason") not in ("stop", None):
                raise ValueError("Truncated response")
            output = choice["message"]["content"]
            normalized_whitespace = False
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError as exc:
                # A complete provider response can contain literal tabs/newlines
                # inside quoted OCR text. Preserve those exact string values;
                # do not repair quotes, escapes, brackets or truncated content.
                if not exc.msg.startswith("Invalid control character") or any(
                    ord(char) < 32 and char not in "\t\r\n" for char in output
                ):
                    raise
                parsed = json.loads(output, strict=False)
                normalized_whitespace = True
            if not isinstance(parsed, dict):
                raise ValueError("Expected object")
            if normalized_whitespace and self.records:
                self.records[-1]["json_normalization"] = "literal_tab_cr_lf_preserved"
            return parsed
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ReviewError("MODEL_RESPONSE_INVALID", "模型返回不是完整 JSON 对象，原始响应已保留") from exc

    def close(self):
        self.client.close()

    def take_records(self):
        records = list(self.records)
        self.records.clear()
        return records
