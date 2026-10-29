import subprocess
import uuid

import pytest
from app.models import ReviewRun
from app.settings import settings
from app.workers.review import execute_run


@pytest.fixture
def content_env(environment, monkeypatch):
    for key in [
        "ark_api_key",
        "ark_text_model",
        "ark_video_model",
        "ark_preprocess_model",
        "asr_api_key",
        "tos_access_key",
        "tos_secret_key",
        "tos_bucket",
    ]:
        monkeypatch.setattr(settings, key, "test-configured")
    monkeypatch.setattr(settings, "video_content_enabled", True)
    monkeypatch.setattr("app.api.video_content.enqueue", lambda run_id: None)

    class Provider:
        def __init__(self, *args):
            pass

        def json(self, instruction, payload, **kwargs):
            name = payload["namespace"]
            duration = payload["duration_ms"]
            return {
                "events": [
                    {
                        "id": name + "_E1",
                        "channel": "spoken_audio",
                        "raw_content": "不好喝。骗你的，好喝。",
                        "start_ms": 0,
                        "end_ms": min(900, duration),
                    }
                ],
                "entities": [],
                "claims": [],
                "limitations": [],
                "boundaries": [],
                "audio_observed": True,
                "inspected_channels": ["spoken_audio", "onscreen_text", "visual_event"],
            }

        def close(self):
            pass

        def take_records(self):
            return []

    monkeypatch.setattr("app.workers.review.Ark", Provider)
    monkeypatch.setattr(
        "app.services.video_content.transcribe",
        lambda *a, **kw: {
            "utterances": [
                {
                    "text": "不好喝。骗你的，好喝。",
                    "start_time": 0,
                    "end_time": 900,
                    "words": [],
                    "additions": {"speaker": 0},
                }
            ],
            "request_id": "test",
            "usage": {},
        },
    )
    return environment


def original(client, project, tmp_path):
    path = tmp_path / "controlled.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=160x120:r=5:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        check=True,
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "video"},
        files={"file": ("controlled.mp4", path.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def start(client, project, asset):
    response = client.post(
        f"/api/v1/projects/{project['id']}/video-contents",
        json={"asset_id": asset["id"], "idempotency_key": str(uuid.uuid4()), "acknowledge_trial": True},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_full_content_flow_preserves_original_and_has_no_human_approval(content_env, project, tmp_path):
    client, sessions = content_env
    asset = original(client, project, tmp_path)
    created = start(client, project, asset)
    execute_run(created["run_id"])
    result = client.get("/api/v1/video-contents/" + created["id"]).json()
    assert result["run_status"] == "COMPLETED", result["error"]
    assert result["status"] == "NEEDS_HUMAN" and result["confirmation"] is None
    assert result["report"]["processing_complete"] and result["report"]["accuracy"] is None
    assert (
        len(result["payload"]["rounds"]) == 4 and result["payload"]["asr_capabilities"]["with_speaker"] == 1
    )
    assert all(e["unit_ids"] for e in result["payload"]["events"])
    archive = client.get("/api/v1/video-contents/" + created["id"] + "/archive")
    assert archive.status_code == 200 and "attachment" in archive.headers["content-disposition"]
    with sessions() as db:
        assert db.get(ReviewRun, created["run_id"]).assessment == "NOT_READY"


def test_correction_creates_new_snapshot_and_keeps_parent_immutable(content_env, project, tmp_path):
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    url = "/api/v1/video-contents/" + created["id"]
    source = client.get(url).json()
    event = source["payload"]["events"][0]
    response = client.post(
        url + "/revisions",
        json={
            "expected_content_hash": source["content_hash"],
            "reason": "Controlled test only, not brand approval",
            "corrections": [{"event_id": event["id"], "raw_content": "人工测试修正"}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["parent_id"] == source["id"] and response.json()["confirmation"] is None
    assert client.get(url).json()["content_hash"] == source["content_hash"]
    assert client.get(url).json()["payload"]["events"][0]["raw_content"] == event["raw_content"]


def test_invalid_revision_time_and_hash_are_blocked(content_env, project, tmp_path):
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    url = "/api/v1/video-contents/" + created["id"]
    source = client.get(url).json()
    event = source["payload"]["events"][0]
    body = {
        "expected_content_hash": source["content_hash"],
        "reason": "test",
        "corrections": [{"event_id": event["id"], "end_ms": 999999}],
    }
    assert client.post(url + "/revisions", json=body).status_code == 422
    body["expected_content_hash"] = "wrong"
    assert client.post(url + "/revisions", json=body).status_code == 409


def test_content_project_authorization(content_env, project, tmp_path):
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    client.delete("/api/v1/session")
    client.post("/api/v1/sessions", json={"username": "test-reviewer", "password": "review-password"})
    for suffix in ["", "/archive"]:
        assert client.get("/api/v1/video-contents/" + created["id"] + suffix).status_code == 403


def test_unfinished_content_cannot_be_confirmed_or_exported(content_env, project, tmp_path):
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    url = "/api/v1/video-contents/" + created["id"]
    assert (
        client.post(
            url + "/confirmation",
            json={"expected_content_hash": "none", "reason": "test", "acknowledge_remaining_gaps": True},
        ).status_code
        == 409
    )
    assert client.get(url + "/archive").status_code == 409


def review_body(client, project, asset, snapshot_id):
    from conftest import frozen_rules

    parsed = client.post(f"/api/v1/assets/{asset['id']}/parse")
    assert parsed.status_code == 202, parsed.text
    execute_run(parsed.json()["run_id"])
    ruleset = frozen_rules(client, project["id"])
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "Controlled content binding only", "video_id": asset["id"]},
    )
    assert submission.status_code == 201, submission.text
    return {
        "submission_id": submission.json()["id"],
        "ruleset_id": ruleset["id"],
        "content_snapshot_id": snapshot_id,
        "historical_trial": True,
        "allow_unverified_audio": True,
        "trial_note": "Controlled engineering test; no actual brand decision",
        "idempotency_key": str(uuid.uuid4()),
    }


def test_review_imports_content_without_rereading_or_promoting_claims(
    content_env, project, tmp_path, monkeypatch
):
    from app.models import Observation, ReviewStage
    from sqlalchemy import select

    client, sessions = content_env
    asset = original(client, project, tmp_path)
    created = start(client, project, asset)
    execute_run(created["run_id"])
    body = review_body(client, project, asset, created["id"])
    response = client.post("/api/v1/video-reviews", json=body)
    assert response.status_code == 202, response.text
    captured = []

    class ComplianceProvider:
        def __init__(self, *args):
            pass

        def json(self, instruction, payload, **kwargs):
            captured.append(payload)
            if "rules" in payload:
                return {
                    "checks": [
                        {
                            "rule_id": payload["rules"][0]["id"],
                            "status": "UNCERTAIN",
                            "evidence_ids": [payload["observations"][0]["id"]],
                            "explanation": "Controlled only",
                        }
                    ]
                }
            return {
                "status": "UNCERTAIN",
                "observations": [],
                "existing_evidence_ids": [],
                "explanation": "Controlled only",
            }

        def take_records(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr("app.workers.review.Ark", ComplianceProvider)
    run_id = response.json()["run_id"]
    execute_run(run_id)
    with sessions() as db:
        run = db.get(ReviewRun, run_id)
        assert run.status == "COMPLETED", run.error
        assert run.result["content_snapshot_id"] == created["id"]
        assert run.assessment != "AI_PASSED"
        names = list(db.scalars(select(ReviewStage.name).where(ReviewStage.run_id == run_id)))
        assert (
            "import_content_snapshot" in names
            and "observe_full" not in names
            and "transcribe_audio" not in names
        )
        observations = list(db.scalars(select(Observation).where(Observation.run_id == run_id)))
        assert observations and all(o.origin["source"] == "video_content_snapshot" for o in observations)
    assert captured[0]["content_context"]["snapshot_id"] == created["id"]
    source = client.get("/api/v1/video-contents/" + created["id"]).json()
    revision = client.post(
        "/api/v1/video-contents/" + created["id"] + "/revisions",
        json={
            "expected_content_hash": source["content_hash"],
            "reason": "Controlled correction invalidates dependent reviews",
            "corrections": [
                {"event_id": source["payload"]["events"][0]["id"], "raw_content": "Controlled changed source"}
            ],
        },
    )
    assert revision.status_code == 201, revision.text
    with sessions() as db:
        assert db.get(ReviewRun, run_id).freshness == "STALE"
    body["idempotency_key"] = str(uuid.uuid4())
    assert client.post("/api/v1/video-reviews", json=body).status_code == 409


def test_review_rejects_incomplete_content(content_env, project, tmp_path):
    client, _ = content_env
    asset = original(client, project, tmp_path)
    created = start(client, project, asset)
    response = client.post("/api/v1/video-reviews", json=review_body(client, project, asset, created["id"]))
    assert response.status_code == 409 and response.json()["code"] == "CONTENT_INCOMPLETE", response.text


def test_review_rejects_other_asset_even_with_same_file_hash(content_env, project, tmp_path):
    client, _ = content_env
    asset = original(client, project, tmp_path)
    created = start(client, project, asset)
    execute_run(created["run_id"])
    other = original(client, project, tmp_path)
    response = client.post("/api/v1/video-reviews", json=review_body(client, project, other, created["id"]))
    assert response.status_code == 409 and response.json()["code"] == "CONTENT_SNAPSHOT_MISMATCH", (
        response.text
    )


def test_content_confirmation_does_not_create_brand_decision(content_env, project, tmp_path):
    from app.models import HumanDecision
    from sqlalchemy import func, select

    client, sessions = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    url = "/api/v1/video-contents/" + created["id"]
    source = client.get(url).json()
    body = {
        "expected_content_hash": source["content_hash"],
        "reason": "Controlled test only, no brand approval",
        "acknowledge_remaining_gaps": True,
    }
    result = client.post(url + "/confirmation", json=body)
    assert result.status_code == 200, result.text
    assert (
        result.json()["status"] == "HUMAN_REVIEWED_WITH_LIMITS" and result.json()["production_ready"] is False
    )
    assert client.post(url + "/confirmation", json=body).status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count(HumanDecision.id))) == 0


def test_schema_reask_preserves_bad_response_and_uses_same_video(content_env, project, tmp_path, monkeypatch):
    import json

    from app.services.storage import local_path
    from app.workers import review

    base = review.Ark
    inputs = []

    class RepairingProvider(base):
        def json(self, instruction, payload, **kwargs):
            result = super().json(instruction, payload, **kwargs)
            if payload["namespace"] == "AF":
                inputs.append({"payload": payload, "video": kwargs["video"]})
                if len(inputs) == 1:
                    result["events"][0]["end_ms"] = 0
            return result

    monkeypatch.setattr(review, "Ark", RepairingProvider)
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    result = client.get("/api/v1/video-contents/" + created["id"]).json()
    assert result["run_status"] == "COMPLETED", result["error"]
    assert len(inputs) == 2 and inputs[0]["video"] == inputs[1]["video"]
    feedback = inputs[1]["payload"]["validation_feedback"]
    assert feedback["rejected_response"]["events"][0]["end_ms"] == 0
    assert feedback["diagnostics"][0]["loc"][:2] == ("events", 0)
    saved = json.loads(local_path(f"runs/{created['run_id']}/content/rejected-AF.json").read_text())
    assert saved["rejected_response"]["events"][0]["end_ms"] == 0
    assert len(result["payload"]["model_call_ledger"]["calls"]) == 4 + result["report"]["unit_count"] + 1


def test_persistent_call_budget_stops_before_model_call(content_env, project, tmp_path, monkeypatch):
    import json

    from app.services.storage import local_path
    from app.workers import review

    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    directory = local_path(f"runs/{created['run_id']}/content")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model-call-ledger.json").write_text(json.dumps({"prior_stage_attempts": 40, "calls": []}))

    class NeverCalled:
        def __init__(self, *args):
            pass

        def json(self, *args, **kwargs):
            raise AssertionError("Paid request must not run")

        def close(self):
            pass

    monkeypatch.setattr(review, "Ark", NeverCalled)
    execute_run(created["run_id"])
    result = client.get("/api/v1/video-contents/" + created["id"]).json()
    assert result["run_status"] == "INCOMPLETE" and result["error"]["code"] == "CONTENT_CALL_BUDGET"


def test_compatible_parser_recovery_reuses_failed_raw_without_rebilling(
    content_env, project, tmp_path, monkeypatch
):
    import json

    from app.models import ReviewStage
    from app.services.storage import local_path
    from app.workers import review

    client, sessions = content_env
    created = start(client, project, original(client, project, tmp_path))
    base = review.Ark
    raw = base(None).json("", {"namespace": "AF", "duration_ms": 1000})
    raw["claims"] = [
        {"kind": "interpretation", "description": "Original candidate retained", "evidence_ids": ["AF_E1"]}
    ]
    directory = local_path(f"runs/{created['run_id']}/content")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "rejected-AF.json").write_text(json.dumps({"rejected_response": raw}))
    (directory / "model-call-ledger.json").write_text(json.dumps({"prior_stage_attempts": 1, "calls": []}))
    with sessions() as db:
        run = db.get(ReviewRun, created["run_id"])
        db.add(
            ReviewStage(
                run_id=run.id, name="content:read:AF", attempt=1, input_hash=run.input_hash, status="FAILED"
            )
        )
        db.commit()

    class RecoveryProvider(base):
        def json(self, instruction, payload, **kwargs):
            assert payload["namespace"] != "AF", "Same accepted raw must not incur another AF request"
            return super().json(instruction, payload, **kwargs)

    monkeypatch.setattr(review, "Ark", RecoveryProvider)
    execute_run(created["run_id"])
    result = client.get("/api/v1/video-contents/" + created["id"]).json()
    assert result["run_status"] == "COMPLETED", result["error"]
    assert any(c.get("id_origin") == "parser_generated_missing_index" for c in result["payload"]["claims"])
    assert (
        "id" not in json.loads((directory / "rejected-AF.json").read_text())["rejected_response"]["claims"][0]
    )


def test_correction_invalidates_entity_and_claim_and_blocks_old_confirmation(
    content_env, project, tmp_path, monkeypatch
):
    from app.workers import review

    base = review.Ark

    class LinkedProvider(base):
        def json(self, instruction, payload, **kwargs):
            result = super().json(instruction, payload, **kwargs)
            prefix = payload["namespace"]
            event = result["events"][0]["id"]
            result["entities"] = [
                {
                    "id": prefix + "_P1",
                    "kind": "object",
                    "description": "Unverified cup",
                    "status": "CANDIDATE",
                    "identity_level": "brand",
                    "basis": "visual",
                    "evidence_ids": [event],
                }
            ]
            result["claims"] = [
                {
                    "id": prefix + "_C1",
                    "kind": "interpretation",
                    "description": "Unverified identity interpretation",
                    "evidence_ids": [event],
                }
            ]
            return result

    monkeypatch.setattr(review, "Ark", LinkedProvider)
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    url = "/api/v1/video-contents/" + created["id"]
    before = client.get(url).json()
    assert before["run_status"] == "COMPLETED", before["error"]
    revised = client.post(
        url + "/revisions",
        json={
            "expected_content_hash": before["content_hash"],
            "reason": "Controlled unreadable package",
            "corrections": [{"event_id": "AF_E1", "status": "UNCLEAR"}],
        },
    )
    assert revised.status_code == 201, revised.text
    body = revised.json()["payload"]
    assert next(e for e in body["entities"] if e["id"] == "AF_P1")["status"] == "UNKNOWN"
    assert next(c for c in body["claims"] if c["id"] == "AF_C1")["requires_recheck"]
    assert (
        next(e for e in client.get(url).json()["payload"]["entities"] if e["id"] == "AF_P1")["status"]
        == "CANDIDATE"
    )
    response = client.post(
        url + "/confirmation",
        json={
            "expected_content_hash": before["content_hash"],
            "reason": "Old version must not be confirmed",
            "acknowledge_remaining_gaps": True,
        },
    )
    assert response.status_code == 409


def test_binding_checks_actual_source_file_hash(content_env, project, tmp_path):
    from app.models import Asset
    from app.services.storage import ensure_local

    client, sessions = content_env
    asset = original(client, project, tmp_path)
    created = start(client, project, asset)
    execute_run(created["run_id"])
    body = review_body(client, project, asset, created["id"])
    with sessions() as db:
        ensure_local(db.get(Asset, asset["id"]).storage_key).write_bytes(b"modified isolated fixture")
    response = client.post("/api/v1/video-reviews", json=body)
    assert response.status_code == 409 and response.json()["code"] == "SOURCE_HASH_CHANGED", response.text


def test_truncation_reask_records_actual_thinking_and_keeps_all_fact_text(
    content_env, project, tmp_path, monkeypatch
):
    from app.errors import ReviewError
    from app.workers import review

    base = review.Ark
    attempts = []

    class TruncatingGlobal(base):
        def json(self, instruction, payload, **kwargs):
            if payload["namespace"] == "G":
                attempts.append((kwargs["thinking"], payload))
                if len(attempts) == 1:
                    raise ReviewError("MODEL_OUTPUT_TRUNCATED", "Controlled output limit")
            return super().json(instruction, payload, **kwargs)

    monkeypatch.setattr(review, "Ark", TruncatingGlobal)
    client, _ = content_env
    created = start(client, project, original(client, project, tmp_path))
    execute_run(created["run_id"])
    result = client.get("/api/v1/video-contents/" + created["id"]).json()
    assert result["run_status"] == "COMPLETED", result["error"]
    assert [a[0] for a in attempts] == ["enabled", "disabled"]
    assert all(e["raw_content"] for e in attempts[1][1]["facts"])
    assert len(attempts[1][1]["facts"]) == len(result["payload"]["events"]) - 1
    assert result["payload"]["model_call_ledger"]["calls"][-1]["thinking"] == "disabled"


def test_visual_inspection_freezes_source_and_never_approves(content_env, project, tmp_path, monkeypatch):
    client, env = content_env
    asset = original(client, project, tmp_path)
    content = start(client, project, asset)
    execute_run(content["run_id"])
    source = client.get(f"/api/v1/video-contents/{content['id']}").json()
    body = {
        "asset_id": asset["id"],
        "idempotency_key": str(uuid.uuid4()),
        "acknowledge_trial": True,
        "expected_content_hash": source["content_hash"],
    }
    endpoint = f"/api/v1/video-contents/{source['id']}/visual-inspections"
    response = client.post(endpoint, json=body)
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert client.post(endpoint, json=body).json()["run_id"] == run_id
    from app.services.visual_inspection import CATEGORIES

    calls = []

    def visual(*args, **kwargs):
        assert kwargs["video"].suffix == ".mp4"
        assert kwargs["fps"] == 5
        calls.append(kwargs["video"])
        if "finding" in args[2]:
            return {"support": "contradicts", "observation": "连续区间没有相应遮挡证据"}
        return {
            "findings": [
                {
                    "category": c,
                    "status": "ISSUE" if c == "product_visibility" else "UNCERTAIN",
                    "title": "待核对",
                    "reason": "未有参考样例",
                    "start_ms": 0,
                    "end_ms": 800,
                }
                for c in CATEGORIES
            ],
            "limitations": ["需人审"],
        }

    monkeypatch.setattr("app.workers.review.Ark.json", visual)
    execute_run(run_id)
    result = client.get(endpoint).json()["items"][0]
    assert result["status"] == "COMPLETED", result
    assert result["result"]["approval"] is False
    assert result["result"]["content_hash"] == source["content_hash"]
    assert len(calls) == 3
    assert any(
        f.get("interval_recheck", {}).get("support") == "contradicts" and f["status"] == "UNCERTAIN"
        for f in result["result"]["findings"]
    )
    assert len(result["result"]["findings"]) == 5
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/decisions",
            json={
                "decision": "APPROVE",
                "reason": "test",
                "expected_revision": 1,
                "expected_input_hash": "test",
            },
        ).status_code
        == 409
    )
    wrong = {**body, "idempotency_key": str(uuid.uuid4()), "expected_content_hash": "wrong"}
    assert client.post(endpoint, json=wrong).status_code == 409


def test_visual_inspection_requires_authentication(content_env):
    client, _ = content_env
    client.cookies.clear()
    assert client.get("/api/v1/video-contents/not-owned/visual-inspections").status_code == 401


def test_visual_invalid_response_reasks_once_then_stops(content_env, project, tmp_path, monkeypatch):
    client, _ = content_env
    asset = original(client, project, tmp_path)
    content = start(client, project, asset)
    execute_run(content["run_id"])
    source = client.get(f"/api/v1/video-contents/{content['id']}").json()
    calls = []

    def invalid(*args, **kwargs):
        calls.append(args[2])
        return {"findings": [], "limitations": []}

    monkeypatch.setattr("app.workers.review.Ark.json", invalid)
    endpoint = f"/api/v1/video-contents/{content['id']}/visual-inspections"
    created = client.post(
        endpoint,
        json={
            "asset_id": asset["id"],
            "expected_content_hash": source["content_hash"],
            "idempotency_key": str(uuid.uuid4()),
            "acknowledge_trial": True,
        },
    )
    execute_run(created.json()["run_id"])
    row = client.get(endpoint).json()["items"][0]
    assert row["status"] == "INCOMPLETE"
    assert row["error"]["code"] == "VISUAL_RESPONSE_INVALID"
    assert len(calls) == 2
    assert calls[1]["validation_feedback"]["previous_response"]["findings"] == []
