from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.models import ReviewRun
from app.workers.review import execute_run
from conftest import frozen_rules, upload_text


def test_two_simultaneous_decisions_only_one_revision_wins(environment, project):
    client, _ = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "人工独立审核并发测试")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "并发测试", "script_id": script["asset"]["id"]},
    ).json()
    run_id = client.post(
        "/api/v1/human-reviews",
        json={
            "submission_id": submission["id"],
            "ruleset_id": ruleset["id"],
            "idempotency_key": str(uuid4()),
        },
    ).json()["run_id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    body = {
        "expected_input_hash": run["input_hash"],
        "expected_revision": run["revision"],
        "decision": "APPROVE",
        "mode": "independent_human",
        "reason": "并发状态测试，不是业务批准",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: client.post(f"/api/v1/runs/{run_id}/decisions", json=body), range(2))
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert len(client.get(f"/api/v1/runs/{run_id}").json()["decisions"]) == 1


def test_approved_script_can_be_used_when_only_video_snapshot_is_added(environment, project, monkeypatch, sample_video):
    client, sessions = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "审批范围测试脚本")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "状态测试", "script_id": script["asset"]["id"]},
    ).json()
    # A completed precondition fixture, confined to the temporary PostgreSQL test schema.
    with sessions() as db:
        run = ReviewRun(
            project_id=project["id"],
            submission_id=submission["id"],
            ruleset_id=ruleset["id"],
            kind="script",
            input_snapshot={"mode": "live", "test_precondition": True},
            input_hash="test-only-hash",
            idempotency_key=str(uuid4()),
            status="COMPLETED",
            assessment="AI_PASSED",
        )
        db.add(run)
        db.commit()
        run_id = run.id
    approval = client.post(
        "/api/v1/script-approvals",
        json={
            "run_id": run_id,
            "expected_input_hash": "test-only-hash",
            "expected_revision": 1,
            "reason": "仅验证审批作用域的测试前置状态",
        },
    ).json()

    path = sample_video
    video = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "video"},
        files={"file": (path.name, path.read_bytes(), "video/mp4")},
    ).json()
    parse = client.post(f"/api/v1/assets/{video['id']}/parse").json()
    execute_run(parse["run_id"])
    revised = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={
            "creator": "状态测试",
            "parent_id": submission["id"],
            "script_id": script["asset"]["id"],
            "video_id": video["id"],
            "pairing_confirmed": True,
        },
    ).json()
    assert client.get(f"/api/v1/runs/{run_id}").json()["freshness"] == "STALE"
    from app.settings import settings

    for field in (
        "ark_api_key",
        "ark_text_model",
        "ark_video_model",
        "ark_preprocess_model",
        "asr_api_key",
        "tos_access_key",
        "tos_secret_key",
        "tos_bucket",
    ):
        monkeypatch.setattr(settings, field, "explicit-test-fixture-not-real")
    monkeypatch.setattr(settings, "ark_video_audio_verified", True)
    response = client.post(
        "/api/v1/video-reviews",
        json={
            "submission_id": revised["id"],
            "ruleset_id": ruleset["id"],
            "script_approval_id": approval["id"],
            "historical_trial": False,
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
