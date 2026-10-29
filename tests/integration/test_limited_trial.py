from uuid import uuid4

import pytest
from app.models import Asset, Project, ReviewRun, RuleSet
from app.settings import settings
from app.workers.review import execute_run
from conftest import frozen_rules, rule_for, upload_text


def partial_draft(client, sessions, project_id, status="partial"):
    source = upload_text(client, project_id)
    draft = client.post(
        f"/api/v1/projects/{project_id}/rulesets/manual",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    edited = client.patch(
        f"/api/v1/rulesets/{draft['id']}",
        json={"expected_revision": 1, "rules": [rule_for(source["blocks"][0]["id"])], "conflicts": []},
    ).json()
    with sessions() as db:
        asset = db.get(Asset, source["asset"]["id"])
        asset.parse_status = "PARTIAL"
        asset.coverage = [{"page": 1, "status": status}]
        ruleset = db.get(RuleSet, draft["id"])
        ruleset.extraction_coverage = [
            {"asset_id": asset.id, "unit": 1, "status": status, "limitations": ["示例小字不清"]}
        ]
        db.commit()
    return edited, source


@pytest.mark.parametrize(
    "accept,status,expected", [(False, "partial", 422), (True, "failed", 422), (True, "partial", 200)]
)
def test_partial_source_is_explicit_trial_only_and_never_human_ack(
    environment, project, accept, status, expected
):
    client, sessions = environment
    draft, source = partial_draft(client, sessions, project["id"], status)
    response = client.post(
        f"/api/v1/rulesets/{draft['id']}/confirm",
        json={
            "expected_revision": 2,
            "kind": "experimental",
            "accept_partial_sources": accept,
            "note": "受限试审，保留示例小字未读清，不声明人工核对。",
        },
    )
    assert response.status_code == expected, response.text
    with sessions() as db:
        assert not db.get(Asset, source["asset"]["id"]).metadata_json.get("human_coverage_ack")
    if expected == 200:
        assert response.json()["extraction_coverage"][0]["status"] == "partial"


@pytest.mark.parametrize("kind,mode", [("brand_confirmed", "trial"), ("experimental", "formal")])
def test_source_limits_cannot_be_accepted_for_brand_or_formal(environment, project, kind, mode):
    client, sessions = environment
    draft, _ = partial_draft(client, sessions, project["id"])
    with sessions() as db:
        db.get(Project, project["id"]).mode = mode
        db.commit()
    response = client.post(
        f"/api/v1/rulesets/{draft['id']}/confirm",
        json={
            "expected_revision": 2,
            "kind": kind,
            "accept_partial_sources": True,
            "note": "试图用受限规范走正式流程，不应允许。",
        },
    )
    assert response.status_code == 409 and response.json()["code"] == "TRIAL_ONLY"


def test_limited_script_completion_cannot_auto_pass(environment, project, monkeypatch):
    client, sessions = environment
    draft, _ = partial_draft(client, sessions, project["id"])
    ruleset = client.post(
        f"/api/v1/rulesets/{draft['id']}/confirm",
        json={
            "expected_revision": 2,
            "accept_partial_sources": True,
            "note": "受限试审保留小字限制，非人工来源确认。",
        },
    ).json()
    script = upload_text(client, project["id"], "script", "测试原文。")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "隔离测试", "script_id": script["asset"]["id"]},
    ).json()
    monkeypatch.setattr(settings, "ark_api_key", "test-not-real")
    monkeypatch.setattr(settings, "ark_text_model", "test-not-real")

    class Provider:
        def __init__(self, path):
            pass

        def close(self):
            pass

    def completed(db, run, provider):
        run.assessment = "AI_PASSED"
        return {"mode": "fixture", "limits": []}

    monkeypatch.setattr("app.workers.review.Ark", Provider)
    monkeypatch.setattr("app.workers.review.script_review", completed)
    response = client.post(
        "/api/v1/script-reviews",
        json={
            "submission_id": submission["id"],
            "ruleset_id": ruleset["id"],
            "idempotency_key": str(uuid4()),
        },
    )
    execute_run(response.json()["run_id"])
    run = client.get(f"/api/v1/runs/{response.json()['run_id']}").json()
    assert run["status"] == "COMPLETED" and run["assessment"] == "NEEDS_HUMAN"
    assert "示例小字不清" in run["result"]["limits"][0]


def test_unverified_audio_option_cannot_hide_missing_credentials(environment, project, monkeypatch):
    client, _ = environment
    ruleset = frozen_rules(client, project["id"])
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "video"},
        files={"file": ("fixture.mp4", b"\x00\x00\x00\x18ftypisom" + bytes(100), "video/mp4")},
    ).json()
    # Media decoding is intentionally not under test here; model configuration is.
    _, sessions = environment
    with sessions() as db:
        db.get(Asset, asset["id"]).parse_status = "COMPLETED"
        db.commit()
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={
            "creator": "隔离测试",
            "video_id": asset["id"],
        },
    ).json()
    body = {
        "submission_id": submission["id"],
        "ruleset_id": ruleset["id"],
        "idempotency_key": str(uuid4()),
        "historical_trial": True,
        "allow_unverified_audio": True,
        "trial_note": "仅做受限试审，音画仍待人工核对。",
    }
    response = client.post("/api/v1/video-reviews", json=body)
    assert response.status_code == 409 and response.json()["code"] == "MODEL_NOT_CONFIGURED"
    assert "ARK_API_KEY" in response.json()["details"]["missing"]
    for name in (
        "ark_api_key",
        "ark_text_model",
        "ark_video_model",
        "ark_preprocess_model",
        "asr_api_key",
        "tos_access_key",
        "tos_secret_key",
        "tos_bucket",
    ):
        monkeypatch.setattr(settings, name, "test-fixture-not-real")
    monkeypatch.setattr(settings, "ark_video_audio_verified", False)
    accepted = client.post("/api/v1/video-reviews", json=body)
    assert accepted.status_code == 202, accepted.text
    run = client.get(f"/api/v1/runs/{accepted.json()['run_id']}").json()
    assert run["input_snapshot"]["allow_unverified_audio"]
    assert run["input_snapshot"]["trial_limits"]
    assert settings.ark_video_audio_verified is False
    with sessions() as db:
        stored = db.get(ReviewRun, run["id"])
        stored.status = "INCOMPLETE"
        db.commit()
    retried = client.post(f"/api/v1/runs/{run['id']}/retry", json={"expected_revision": run["revision"]})
    assert retried.status_code == 202, retried.text

    class Provider:
        def __init__(self, path):
            pass

        def close(self):
            pass

    def completed(db, run, provider):
        run.assessment = "AI_PASSED"
        return {"mode": "fixture", "limits": []}

    monkeypatch.setattr("app.workers.review.Ark", Provider)
    monkeypatch.setattr("app.workers.review.video_review", completed)
    execute_run(run["id"])
    completed_run = client.get(f"/api/v1/runs/{run['id']}").json()
    assert completed_run["status"] == "COMPLETED" and completed_run["assessment"] == "NEEDS_HUMAN"
    assert settings.ark_video_audio_verified is False
    assert "受限技术试审" in client.get(f"/api/v1/runs/{run['id']}/report").text
    body["idempotency_key"] = str(uuid4())
    body["historical_trial"] = False
    response = client.post("/api/v1/video-reviews", json=body)
    assert response.status_code == 409 and response.json()["code"] == "TRIAL_ONLY"
