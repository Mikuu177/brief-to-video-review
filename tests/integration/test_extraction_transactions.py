import io
from uuid import uuid4

import fitz
import pytest
from app.errors import ReviewError
from app.models import ReviewRun, ReviewStage, SourceBlock
from app.services.runs import stage
from app.settings import settings
from app.workers.review import execute_run
from conftest import rule_for, upload_text
from PIL import Image
from sqlalchemy import select


def configure_extraction(monkeypatch, rule, before_resolution=lambda: None):
    """Explicit provider fixture: transaction checks only, no AI quality claim."""
    monkeypatch.setattr(settings, "ark_api_key", "test-fixture-not-real")
    monkeypatch.setattr(settings, "ark_text_model", "test-fixture-not-real")

    class ProviderFixture:
        def __init__(self, path):
            pass

        def json(self, instruction, payload, **kwargs):
            if "source_blocks" in payload:
                return {"rules": [rule], "conflicts": []}
            before_resolution()
            return {"conflicts": []}

        def take_records(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr("app.workers.review.Ark", ProviderFixture)


@pytest.mark.parametrize("action", ["edit_during_call", "freeze_during_call", "edit_before_start"])
def test_extraction_never_overwrites_changed_or_frozen_draft(environment, project, monkeypatch, action):
    client, _ = environment
    source = upload_text(client, project["id"])
    rule = rule_for(source["blocks"][0]["id"])
    run_id = ruleset_id = None

    def human_edit():
        edited = {**rule, "title": "人工已经确认的新口径"}
        response = client.patch(
            f"/api/v1/rulesets/{ruleset_id}",
            json={"expected_revision": 1, "rules": [edited], "conflicts": []},
        )
        assert response.status_code == 200, response.text
        if action == "freeze_during_call":
            covered = client.post(
                f"/api/v1/rulesets/{ruleset_id}/manual-coverage",
                json={"expected_revision": 2, "note": "人工已检查这份单行隔离测试Brief。"},
            ).json()
            frozen = client.post(
                f"/api/v1/rulesets/{ruleset_id}/confirm",
                json={
                    "expected_revision": covered["revision"],
                    "kind": "experimental",
                    "note": "明确冻结人工版本，后台旧提取不得覆盖。",
                },
            )
            assert frozen.status_code == 200, frozen.text

    configure_extraction(monkeypatch, rule, human_edit if action != "edit_before_start" else lambda: None)
    created = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/extract",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    )
    assert created.status_code == 202, created.text
    run_id, ruleset_id = created.json()["run_id"], created.json()["ruleset_id"]
    if action == "edit_before_start":
        human_edit()
    execute_run(run_id)
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "INCOMPLETE"
    assert run["error"]["code"] == "VERSION_CONFLICT"
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    draft = next(r for r in detail["rulesets"] if r["id"] == ruleset_id)
    assert draft["rules"][0]["title"] == "人工已经确认的新口径"
    assert draft["status"] == ("FROZEN" if action == "freeze_during_call" else "DRAFT")
    retry = client.post(f"/api/v1/runs/{run_id}/retry", json={"expected_revision": run["revision"]})
    assert retry.status_code == 409 and retry.json()["code"] == "VERSION_CONFLICT"


def test_unchanged_extraction_publishes_pending_rules_with_bound_revision(environment, project, monkeypatch):
    client, _ = environment
    source = upload_text(client, project["id"])
    configure_extraction(monkeypatch, rule_for(source["blocks"][0]["id"]))
    created = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/extract",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    execute_run(created["run_id"])
    run = client.get(f"/api/v1/runs/{created['run_id']}").json()
    assert run["status"] == "COMPLETED"
    assert run["input_snapshot"]["ruleset_revision"] == 1
    assert run["rules"][0]["confirmation_status"] == "pending"
    assert run["assessment"] == "NOT_READY"


def test_cancellation_during_call_rolls_back_stage_side_effects(environment, project):
    client, sessions = environment
    source = upload_text(client, project["id"])
    with sessions() as db:
        run = ReviewRun(
            project_id=project["id"],
            kind="extract",
            input_snapshot={},
            input_hash="transaction-test",
            idempotency_key=str(uuid4()),
            status="RUNNING",
        )
        db.add(run)
        db.commit()

        def canceled_call():
            db.add(
                SourceBlock(
                    asset_id=source["asset"]["id"],
                    text="取消后不得提交的视觉文字",
                    locator={"kind": "txt", "line_start": 2, "line_end": 2},
                    extraction_method="explicit_test_fixture",
                    coverage_status="success",
                )
            )
            db.flush()
            assert client.post(f"/api/v1/runs/{run.id}/cancel").status_code == 200
            return {"test_only": True}

        with pytest.raises(ReviewError, match="取消"):
            stage(db, run, "provider_call", canceled_call)
        assert db.scalar(select(SourceBlock).where(SourceBlock.text == "取消后不得提交的视觉文字")) is None
        saved = db.scalar(select(ReviewStage).where(ReviewStage.run_id == run.id))
        assert saved.status == "FAILED" and saved.error["code"] == "CANCELED"


def test_full_manual_coverage_acknowledges_embedded_pdf_image(environment, project):
    client, _ = environment
    image = io.BytesIO()
    Image.new("RGB", (24, 24), "green").save(image, format="PNG")
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Product name must appear.")
        page.insert_image(fitz.Rect(72, 90, 120, 138), stream=image.getvalue())
        content = document.tobytes()
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "brief"},
        files={"file": ("visual-coverage.pdf", content, "application/pdf")},
    ).json()
    parse = client.post(f"/api/v1/assets/{uploaded['id']}/parse").json()
    execute_run(parse["run_id"])
    source = client.get(f"/api/v1/assets/{uploaded['id']}/blocks").json()
    assert any(item.get("visual_review") == "pending" for item in source["coverage"])
    draft = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/manual",
        json={"source_ids": [uploaded["id"]], "idempotency_key": str(uuid4())},
    ).json()
    rule = rule_for(source["blocks"][0]["id"], quote="Product name must appear.")
    client.patch(
        f"/api/v1/rulesets/{draft['id']}", json={"expected_revision": 1, "rules": [rule], "conflicts": []}
    )
    confirm = {"expected_revision": 2, "note": "测试，不应跳过图片核对。", "kind": "experimental"}
    before = client.post(f"/api/v1/rulesets/{draft['id']}/confirm", json=confirm)
    assert before.status_code == 422 and before.json()["code"] == "PARSE_INCOMPLETE"
    covered = client.post(
        f"/api/v1/rulesets/{draft['id']}/manual-coverage",
        json={"expected_revision": 2, "note": "已回看此隔离PDF文字和图片，明确记录人工完整核对声明。"},
    ).json()
    confirm["expected_revision"] = covered["revision"]
    after = client.post(f"/api/v1/rulesets/{draft['id']}/confirm", json=confirm)
    assert after.status_code == 200, after.text
    asset = client.get(f"/api/v1/projects/{project['id']}").json()["assets"][0]
    assert asset["metadata"]["human_coverage_ack"]["actor"] == "test-admin"
    assert asset["coverage"][0]["visual_review"] == "pending"  # No fake OCR success.


def test_cancel_after_parse_commit_preserves_asset_and_retry_reuses_it(environment, project, monkeypatch):
    client, _ = environment
    from app.workers import review

    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "brief"},
        files={"file": ("committed.txt", b"Already parsed source.", "text/plain")},
    ).json()
    run_id = client.post(f"/api/v1/assets/{uploaded['id']}/parse").json()["run_id"]
    real_stage = review.stage

    def cancel_after_commit(db, run, name, function):
        output = real_stage(db, run, name, function)
        assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
        return output

    monkeypatch.setattr(review, "stage", cancel_after_commit)
    execute_run(run_id)
    canceled = client.get(f"/api/v1/runs/{run_id}").json()
    assert canceled["status"] == "CANCELED"
    source = client.get(f"/api/v1/assets/{uploaded['id']}/blocks").json()
    assert source["asset"]["parse_status"] == "COMPLETED" and len(source["blocks"]) == 1
    monkeypatch.setattr(review, "stage", real_stage)
    retry = client.post(f"/api/v1/runs/{run_id}/retry", json={"expected_revision": canceled["revision"]})
    assert retry.status_code == 202, retry.text
    execute_run(run_id)
    assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "COMPLETED"
    assert len(client.get(f"/api/v1/assets/{uploaded['id']}/blocks").json()["blocks"]) == 1


@pytest.mark.parametrize("readable", [True, False])
def test_visual_limits_preserve_draft_and_freeze_gate(environment, project, monkeypatch, readable):
    client, _ = environment
    image = io.BytesIO()
    Image.new("RGB", (24, 24), "green").save(image, format="PNG")
    with fitz.open() as document:
        first = document.new_page()
        if readable:
            first.insert_text((72, 72), "Product name must appear.")
        first.insert_image(fitz.Rect(72, 90, 120, 138), stream=image.getvalue())
        second = document.new_page()
        second.insert_text((72, 72), "Product name must appear.")
        content = document.tobytes()
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "brief"},
        files={"file": ("partial-visual.pdf", content, "application/pdf")},
    ).json()
    parse = client.post(f"/api/v1/assets/{uploaded['id']}/parse").json()
    execute_run(parse["run_id"])
    seen_pages = []
    monkeypatch.setattr(settings, "ark_api_key", "test-fixture-not-real")
    monkeypatch.setattr(settings, "ark_text_model", "test-fixture-not-real")

    class ProviderFixture:
        def __init__(self, path):
            pass

        def json(self, instruction, payload, **kwargs):
            if "page" in payload:
                seen_pages.append(payload["page"])
                return {
                    "text": "Product name must appear." if readable or payload["page"] == 2 else "",
                    "limitations": ["Unclear sample label"] if payload["page"] == 1 else [],
                }
            if "source_blocks" in payload:
                source = payload["source_blocks"][0]
                return {
                    "rules": [rule_for(source["id"], quote="Product name must appear.")],
                    "conflicts": [],
                }
            return {"conflicts": []}

        def take_records(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr("app.workers.review.Ark", ProviderFixture)
    created = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/extract",
        json={"source_ids": [uploaded["id"]], "idempotency_key": str(uuid4())},
    ).json()
    execute_run(created["run_id"])
    run = client.get(f"/api/v1/runs/{created['run_id']}").json()
    if not readable:
        assert run["status"] == "INCOMPLETE" and run["error"]["code"] == "PARSE_INCOMPLETE"
        assert seen_pages == [1] and not run["rules"]
        return
    assert run["status"] == "COMPLETED" and run["assessment"] == "NOT_READY"
    assert seen_pages == [1, 2] and len(run["rules"]) == 2
    assert run["result"]["coverage_complete"] is False
    assert run["result"]["limits"] == ["页/表 1: Unclear sample label"]
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    asset = next(a for a in detail["assets"] if a["id"] == uploaded["id"])
    assert asset["parse_status"] == "PARTIAL"
    assert [page["status"] for page in asset["coverage"]] == ["partial", "success"]
    assert asset["coverage"][0]["limitations"] == ["Unclear sample label"]
    draft = next(r for r in detail["rulesets"] if r["id"] == created["ruleset_id"])
    rules = [{**rule, "confirmation_status": "confirmed"} for rule in draft["rules"]]
    updated = client.patch(
        f"/api/v1/rulesets/{draft['id']}",
        json={"expected_revision": draft["revision"], "rules": rules, "conflicts": []},
    ).json()
    frozen = client.post(
        f"/api/v1/rulesets/{draft['id']}/confirm",
        json={"expected_revision": updated["revision"], "kind": "experimental", "note": "Test only"},
    )
    assert frozen.status_code == 422 and frozen.json()["code"] == "PARSE_INCOMPLETE"
