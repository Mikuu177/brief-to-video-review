import json

import httpx
import pytest
from app.errors import ReviewError
from app.providers import asr
from app.settings import settings


@pytest.fixture
def asr_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "asr_api_key", "test-secret")
    monkeypatch.setattr(settings, "asr_resource_id", "volc.seedasr.auc")
    monkeypatch.setattr(settings, "asr_local_address", "")
    monkeypatch.setattr(asr, "external_audio_url", lambda path: "https://private-audio?signature=private")
    monkeypatch.setattr(asr.time, "sleep", lambda seconds: None)
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio test fixture")
    return path, tmp_path / "raw"


def response(code="20000000", status=200, body=None):
    return httpx.Response(
        status,
        headers={"X-Api-Status-Code": code, "X-Tt-Logid": "log-123"},
        json=body
        if body is not None
        else {"result": {"utterances": [{"text": "原话", "start_time": 0, "end_time": 900}]}},
    )


def test_standard_submit_queries_same_task_and_preserves_original_speech(asr_setup, monkeypatch):
    calls = []
    responses = iter([response(), response("20000002"), response("20000001"), response()])

    def post(url, **kwargs):
        calls.append((url, dict(kwargs["headers"]), kwargs["json"]))
        return next(responses)

    monkeypatch.setattr(asr.httpx, "post", post)
    result = asr.transcribe(*asr_setup)
    assert [c[0].split("/")[-1] for c in calls] == ["submit", "query", "query", "query"]
    assert len({c[1]["X-Api-Request-Id"] for c in calls}) == 1
    assert all(c[1]["X-Api-Resource-Id"] == "volc.seedasr.auc" for c in calls)
    assert calls[1][1]["X-Tt-Logid"] == "log-123"
    assert calls[0][2]["request"]["enable_ddc"] is False
    assert calls[0][2]["request"]["enable_itn"] is False
    assert all(c[2] == {} for c in calls[1:])
    assert result["utterances"][0]["text"] == "原话"
    task = json.loads(next(asr_setup[1].glob("*-task.json")).read_text())
    assert task["status"] == "COMPLETED"
    assert "test-secret" not in json.dumps(task) and "signature" not in json.dumps(task)


def test_turbo_keeps_flash_protocol(asr_setup, monkeypatch):
    monkeypatch.setattr(settings, "asr_resource_id", "volc.bigasr.auc_turbo")
    calls = []
    monkeypatch.setattr(asr.httpx, "post", lambda url, **kwargs: calls.append(url) or response())
    asr.transcribe(*asr_setup)
    assert calls == [asr.BASE_URL + "/recognize/flash"]


def test_permission_failure_has_no_fallback_or_resubmission(asr_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(
        asr.httpx, "post", lambda url, **kwargs: calls.append(url) or response("45000030", 403)
    )
    with pytest.raises(ReviewError) as exc:
        asr.transcribe(*asr_setup)
    assert exc.value.code == "ASR_FAILED" and len(calls) == 1


def test_query_timeout_keeps_task_id_without_resubmission(asr_setup, monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if url.endswith("/submit"):
            return response()
        raise httpx.ReadTimeout("provider may still be processing")

    monkeypatch.setattr(asr.httpx, "post", post)
    with pytest.raises(ReviewError) as exc:
        asr.transcribe(*asr_setup)
    task = json.loads(next(asr_setup[1].glob("*-task.json")).read_text())
    assert exc.value.unknown_remote and len(calls) == 2
    assert task["request_id"] and task["status"] == "UNKNOWN_REMOTE"


def test_unsupported_resource_is_rejected_before_upload(asr_setup, monkeypatch):
    monkeypatch.setattr(settings, "asr_resource_id", "unadapted-resource")
    monkeypatch.setattr(asr, "external_audio_url", lambda path: pytest.fail("must not upload"))
    with pytest.raises(ReviewError) as exc:
        asr.transcribe(*asr_setup)
    assert exc.value.code == "ASR_RESOURCE_UNSUPPORTED"


def test_success_without_utterances_is_not_accepted(asr_setup, monkeypatch):
    monkeypatch.setattr(
        asr.httpx, "post", lambda url, **kwargs: response(body={"result": {"text": "No timing"}})
    )
    with pytest.raises(ReviewError) as exc:
        asr.transcribe(*asr_setup)
    assert exc.value.code == "MODEL_RESPONSE_INVALID"


def test_standard_deadline_does_not_submit_again(asr_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "provider_timeout_seconds", 10)
    ticks = iter([0, 0, 11, 12])
    monkeypatch.setattr(asr.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(asr.httpx, "post", lambda url, **kwargs: calls.append(url) or response())
    with pytest.raises(ReviewError) as exc:
        asr.transcribe(*asr_setup)
    assert exc.value.unknown_remote and len(calls) == 1
    assert calls[0].endswith("/submit")


def test_query_failure_resumes_same_task_without_upload_or_resubmit(asr_setup, monkeypatch):
    calls = []

    def fail_query(url, **kwargs):
        calls.append((url, kwargs["headers"]["X-Api-Request-Id"]))
        if url.endswith("/submit"):
            return response()
        raise httpx.ReadError("test-only interrupted query")

    monkeypatch.setattr(asr.httpx, "post", fail_query)
    with pytest.raises(ReviewError):
        asr.transcribe(*asr_setup)
    request_id = calls[0][1]
    monkeypatch.setattr(asr, "external_audio_url", lambda path: pytest.fail("must not upload again"))
    monkeypatch.setattr(
        asr.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs["headers"]["X-Api-Request-Id"])) or response(),
    )
    recovered = asr.transcribe(*asr_setup)
    assert recovered["reused_task"] and recovered["request_id"] == request_id
    assert [url.split("/")[-1] for url, _ in calls] == ["submit", "query", "query"]
    count = len(calls)
    assert asr.transcribe(*asr_setup)["request_id"] == request_id
    assert len(calls) == count  # Exact audio/resource completed response reuse.


def test_changed_audio_does_not_reuse_completed_transcript(asr_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(asr.httpx, "post", lambda url, **kwargs: calls.append(url) or response())
    first = asr.transcribe(*asr_setup)
    asr_setup[0].write_bytes(b"different audio fixture")
    second = asr.transcribe(*asr_setup)
    assert first["request_id"] != second["request_id"]
    assert sum(url.endswith("/submit") for url in calls) == 2
