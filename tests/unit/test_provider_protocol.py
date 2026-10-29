import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.errors import ReviewError
from app.providers.ark import Ark
from app.settings import Settings, settings
from pydantic import ValidationError


def provider_with_transport(tmp_path, handler):
    provider = Ark(tmp_path)
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_429_has_bounded_retries_and_trace_without_authorization(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("app.providers.ark.time.sleep", lambda seconds: None)

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429 if len(calls) < 3 else 200, json={"id": "request-123", "usage": {"total_tokens": 12}}
        )

    provider = provider_with_transport(tmp_path, handler)
    assert provider.request("POST", "/chat/completions", json={})["id"] == "request-123"
    assert len(calls) == 3 and provider.records[0]["attempt"] == 3
    assert all("Authorization" not in file.read_text() for file in tmp_path.glob("*.json"))
    provider.close()


def test_authentication_failure_and_timeout_are_not_blindly_retried(tmp_path, monkeypatch):
    calls = []

    def unauthorized(request):
        calls.append(request)
        return httpx.Response(401, json={"secret_echo": "must-not-show"})

    provider = provider_with_transport(tmp_path, unauthorized)
    with pytest.raises(ReviewError) as error:
        provider.request("POST", "/chat/completions", json={})
    assert len(calls) == 1 and "must-not-show" not in str(error.value)
    provider.close()
    calls.clear()

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("simulated timeout")

    provider = provider_with_transport(tmp_path, timeout)
    with pytest.raises(ReviewError) as error:
        provider.request("POST", "/chat/completions", json={})
    assert len(calls) == 1 and error.value.unknown_remote
    provider.close()


def test_video_input_is_native_mp4_reference_not_image_list(tmp_path, monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "r",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"observations": []}'}}],
            },
        )

    provider = provider_with_transport(tmp_path / "raw", handler)
    monkeypatch.setattr(provider, "upload_video", lambda path, fps: "file-original-mp4")
    path = tmp_path / "original.mp4"
    path.write_bytes(b"test fixture mp4 reference; not real media evaluation")
    provider.json("Neutral observation", {"duration_ms": 36000}, video=path, fps=2)
    parts = captured[0]["messages"][1]["content"]
    assert parts[0] == {"type": "video_url", "video_url": {"file_id": "file-original-mp4"}}
    assert not any(item["type"] == "image_url" for item in parts)
    assert "approved_script" not in parts[-1]["text"]
    assert "thinking" not in captured[0]  # Existing video requests keep model defaults.
    assert captured[0]["max_completion_tokens"] == 32768
    assert "max_tokens" not in captured[0]
    provider.close()


def test_native_file_reuse_requires_same_content_model_and_fps(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        number = sum(method == "POST" for method, _ in requests)
        return httpx.Response(200, json={"id": f"file-{number}", "status": "active"})

    provider = provider_with_transport(tmp_path / "raw", handler)
    video = tmp_path / "original.mp4"
    video.write_bytes(b"not a real model evaluation")
    assert provider.upload_video(video, 2) == provider.upload_video(video, 2)
    assert [method for method, _ in requests] == ["POST", "GET"]
    provider.upload_video(video, 5)
    monkeypatch.setattr(settings, "ark_preprocess_model", "different-test-model")
    provider.upload_video(video, 2)
    video.write_bytes(b"changed content")
    provider.upload_video(video, 2)
    assert sum(method == "POST" for method, _ in requests) == 4
    provider.close()


def test_reuse_transport_failure_never_triggers_duplicate_upload(tmp_path):
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "GET":
            raise httpx.ReadTimeout("test-only")
        return httpx.Response(200, json={"id": "file-test", "status": "active"})

    provider = provider_with_transport(tmp_path / "raw", handler)
    video = tmp_path / "original.mp4"
    video.write_bytes(b"test-only")
    provider.upload_video(video, 2)
    with pytest.raises(ReviewError) as error:
        provider.upload_video(video, 2)
    assert error.value.unknown_remote and requests == ["POST", "GET"]
    provider.close()


@pytest.mark.parametrize("content,finish", [("not-json", "stop"), ("{}", "length"), ("[]", "stop")])
def test_invalid_or_truncated_response_is_not_accepted(tmp_path, content, finish):
    provider = provider_with_transport(
        tmp_path,
        lambda request: httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        ),
    )
    with pytest.raises(ReviewError) as error:
        provider.json("instruction", {})
    assert error.value.code == ("MODEL_OUTPUT_TRUNCATED" if finish == "length" else "MODEL_RESPONSE_INVALID")
    provider.close()


def test_explicit_source_binding_reaches_provider_without_environment_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ark_api_key", "test-only-api-key")
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.client_address[0], self.path))
            body = b'{"id":"local-provider"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(settings, "ark_local_address", "127.0.0.1")
    monkeypatch.setattr(settings, "ark_base_url", f"http://127.0.0.1:{server.server_port}")
    provider = Ark(tmp_path)
    try:
        assert provider.request("GET", "/files/example")["id"] == "local-provider"
        assert received == [("127.0.0.1", "/files/example")]
    finally:
        provider.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_source_binding_requires_numeric_ip_and_is_optional():
    assert Settings(_env_file=None, ark_local_address="").ark_local_address == ""
    assert Settings(_env_file=None, ark_local_address="127.0.0.1").ark_local_address == "127.0.0.1"
    assert Settings(_env_file=None, tos_local_address="127.0.0.1").tos_local_address == "127.0.0.1"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ark_local_address="example.com")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tos_local_address="example.com")


def test_overdue_account_is_distinct_from_credentials_and_is_not_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            403,
            json={"error": {"code": "AccountOverdueError", "message": "must-not-echo-private-input"}},
        )

    provider = provider_with_transport(tmp_path, handler)
    with pytest.raises(ReviewError) as error:
        provider.request("POST", "/chat/completions", json={})
    assert error.value.code == "PROVIDER_ACCOUNT_OVERDUE"
    assert not error.value.unknown_remote and len(calls) == 1
    assert "must-not-echo" not in str(error.value)
    provider.close()


def test_explicit_thinking_mode_is_sent_and_recorded_for_replay(tmp_path):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"citations":[]}'}}]},
        )

    provider = provider_with_transport(tmp_path, handler)
    assert provider.json("Citation repair", {}, thinking="disabled") == {"citations": []}
    assert captured[0]["thinking"] == {"type": "disabled"}
    trace = next(tmp_path.glob("input-*.json"))
    assert json.loads(trace.read_text())["thinking"] == "disabled"
    assert json.loads(trace.read_text())["max_completion_tokens"] == 32768
    provider.close()


def test_output_length_stop_is_distinct_known_and_never_reissued(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": '{"rules":['}}]},
        )

    provider = provider_with_transport(tmp_path, handler)
    with pytest.raises(ReviewError) as error:
        provider.json("Extract rules", {})
    assert error.value.code == "MODEL_OUTPUT_TRUNCATED"
    assert not error.value.unknown_remote
    assert len(calls) == 1
    assert len([p for p in tmp_path.glob("*.json") if not p.name.startswith("input-")]) == 1
    provider.close()


def test_complete_ocr_literal_whitespace_is_preserved_and_audited(tmp_path):
    text = "Literal\ttab\nline\rreturn"
    content = '{"text":"' + text + '","limitations":[]}'
    provider = provider_with_transport(
        tmp_path,
        lambda request: httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        ),
    )
    output = provider.json("Transcribe, do not summarize", {})
    assert output == {"text": text, "limitations": []}
    assert provider.records[-1]["json_normalization"] == "literal_tab_cr_lf_preserved"
    raw = next(p for p in tmp_path.glob("*.json") if not p.name.startswith("input-"))
    assert json.loads(raw.read_text())["choices"][0]["message"]["content"] == content
    provider.close()


@pytest.mark.parametrize(
    "content,finish",
    [
        ('{"text":"literal\ttab"', "stop"),
        ('{"text":"literal\ttab","bad":"\\q"}', "stop"),
        ('{"text":"literal\x00null"}', "stop"),
        ('{"text":"literal\x0bvertical"}', "stop"),
        ('{"text":"literal\ttab"}', "length"),
    ],
)
def test_whitespace_compatibility_cannot_accept_other_invalid_or_truncated_json(tmp_path, content, finish):
    provider = provider_with_transport(
        tmp_path,
        lambda request: httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        ),
    )
    with pytest.raises(ReviewError):
        provider.json("Transcribe", {})
    assert len(provider.records) == 1
    provider.close()
