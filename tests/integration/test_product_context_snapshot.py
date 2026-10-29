from uuid import uuid4

from app.models import Project, ReviewRun
from app.settings import settings
from conftest import frozen_rules, upload_text


def test_review_keeps_campaign_identity_when_project_metadata_changes(environment, project, monkeypatch):
    client, sessions = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "仅测试上下文快照")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={
            "creator": "隔离上下文测试",
            "script_id": script["asset"]["id"],
        },
    ).json()
    monkeypatch.setattr(settings, "ark_api_key", "fixture-only")
    monkeypatch.setattr(settings, "ark_text_model", "fixture-only")
    response = client.post(
        "/api/v1/script-reviews",
        json={
            "submission_id": submission["id"],
            "ruleset_id": ruleset["id"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 202
    with sessions() as db:
        db.get(Project, project["id"]).brand = "ChangedBrand"
        db.commit()
        run = db.get(ReviewRun, response.json()["run_id"])
        assert run.input_snapshot["product_context"] == {"brand": "TestBrand", "product": "TestDrink"}
        assert run.input_snapshot["verification_context_version"] == 3
