import uuid

import pytest
from app.models import DocumentUnit, ReviewRun, SourceManifest, SourceManifestFile
from app.settings import settings
from app.workers.review import execute_run
from sqlalchemy import select


@pytest.fixture
def integrity_environment(environment, monkeypatch):
    monkeypatch.setattr(settings, "source_integrity_enabled", True)
    monkeypatch.setattr("app.api.integrity.enqueue", lambda run_id: None)
    return environment


def create_source(client, project, filename="brief.txt", content="背景\n不得提神。".encode()):
    response = client.post(
        f"/api/v1/projects/{project['id']}/assets",
        data={"kind": "brief"},
        files={"file": (filename, content, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_manifest(client, project, asset, key=None):
    body = {
        "name": "完整来源读取",
        "idempotency_key": key or str(uuid.uuid4()),
        "entries": [{"asset_id": asset["id"], "role": "reference", "platform": "unconfirmed"}],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/source-manifests", json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


def test_local_reading_requires_no_model_and_keeps_legacy_parse(integrity_environment, project, monkeypatch):
    client, sessions = integrity_environment
    asset = create_source(client, project)
    monkeypatch.setattr("app.workers.review.Ark", lambda *a, **kw: pytest.fail("G1 must not call a model"))
    created, _ = create_manifest(client, project, asset)
    execute_run(created["run_id"])
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert detail["report"]["processing_complete"] is True, detail["files"][0]["report"]["issues"]
    assert detail["report"]["production_ready"] is False
    assert detail["report"]["semantic_audit"] == "NOT_STARTED"
    assert detail["report"]["human_approval"] == "NOT_RECORDED"
    assert detail["files"][0]["report"]["processed_roots"] == 2
    refreshed = client.get(f"/api/v1/projects/{project['id']}").json()
    assert next(a for a in refreshed["assets"] if a["id"] == asset["id"])["parse_status"] == "NOT_PARSED"


def test_manifest_idempotency_and_mismatched_body(integrity_environment, project):
    client, _ = integrity_environment
    asset = create_source(client, project)
    created, body = create_manifest(client, project, asset)
    url = f"/api/v1/projects/{project['id']}/source-manifests"
    assert client.post(url, json=body).json() == created
    body["name"] = "different"
    assert client.post(url, json=body).status_code == 409


def test_feature_flag_blocks_creation(integrity_environment, project, monkeypatch):
    client, _ = integrity_environment
    asset = create_source(client, project)
    _, body = create_manifest(client, project, asset)
    monkeypatch.setattr(settings, "source_integrity_enabled", False)
    body["idempotency_key"] = str(uuid.uuid4())
    assert client.post(f"/api/v1/projects/{project['id']}/source-manifests", json=body).status_code == 409


def test_missing_root_cannot_remain_complete(integrity_environment, project):
    client, sessions = integrity_environment
    asset = create_source(client, project)
    created, _ = create_manifest(client, project, asset)
    execute_run(created["run_id"])
    with sessions() as db:
        manifest_file = db.scalar(
            select(SourceManifestFile).where(SourceManifestFile.manifest_id == created["manifest_id"])
        )
        row = db.scalar(select(DocumentUnit).where(DocumentUnit.file_id == manifest_file.id))
        db.delete(row)
        db.commit()
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["report"]["processing_complete"] is False
    assert result["files"][0]["report"]["missing_roots"]
    assert result["files"][0]["report"]["missing_detected_units"]


def test_duplicate_source_and_foreign_asset_rejected(integrity_environment, project):
    client, _ = integrity_environment
    asset = create_source(client, project)
    _, body = create_manifest(client, project, asset)
    body["idempotency_key"] = str(uuid.uuid4())
    body["entries"] *= 2
    assert client.post(f"/api/v1/projects/{project['id']}/source-manifests", json=body).status_code == 422
    foreign = client.post(
        "/api/v1/projects",
        json={
            "name": "other",
            "brand": "b",
            "product": "p",
            "platform": "other",
            "phase": "launch",
            "mode": "trial",
        },
    ).json()
    body["entries"] = body["entries"][:1]
    assert client.post(f"/api/v1/projects/{foreign['id']}/source-manifests", json=body).status_code == 422


def test_unauthorized_project_cannot_read_manifest_or_units(integrity_environment, project):
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    client.delete("/api/v1/session")
    client.post("/api/v1/sessions", json={"username": "test-reviewer", "password": "review-password"})
    assert client.get(f"/api/v1/source-manifests/{created['manifest_id']}").status_code == 403
    assert client.get(f"/api/v1/projects/{project['id']}/source-manifests").status_code == 403


def test_hash_change_blocks_reading(integrity_environment, project):
    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    with sessions() as db:
        entry = db.scalar(
            select(SourceManifestFile).where(SourceManifestFile.manifest_id == created["manifest_id"])
        )
        entry.source_sha256 = "0" * 64
        db.commit()
    execute_run(created["run_id"])
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["status"] == "BLOCKED"
    assert result["report"]["processing_complete"] is False
    assert result["files"][0]["report"]["issues"][0]["code"] == "SOURCE_HASH_CHANGED"


def test_missing_process_monitor_blocks_before_spawning_reader(integrity_environment, project, monkeypatch):
    import shutil

    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "ps" else original_which(name))
    monkeypatch.setattr(
        "app.services.document_inventory.subprocess.Popen",
        lambda *a, **kw: pytest.fail("Reader must not start without its memory monitor"),
    )
    execute_run(created["run_id"])
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert detail["status"] == "BLOCKED"
    assert detail["report"]["processing_complete"] is False
    assert detail["files"][0]["report"]["issues"][0]["code"] == "READER_DEPENDENCY_MISSING"


@pytest.mark.parametrize("run_status", ["INCOMPLETE", "CANCELED"])
def test_interrupted_source_read_has_consistent_list_and_detail_status(
    integrity_environment, project, run_status
):
    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    with sessions() as db:
        db.get(SourceManifest, created["manifest_id"]).status = "READING"
        db.get(ReviewRun, created["run_id"]).status = run_status
        db.commit()
    listing = client.get(f"/api/v1/projects/{project['id']}/source-manifests").json()
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert listing["items"][0]["status"] == detail["status"] == "FAILED"
    assert detail["report"]["processing_complete"] is False


def test_completed_read_is_not_published_twice(integrity_environment, project):
    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    execute_run(created["run_id"])
    with sessions() as db:
        manifest = db.get(SourceManifest, created["manifest_id"])
        entries = list(
            db.scalars(select(SourceManifestFile).where(SourceManifestFile.manifest_id == manifest.id))
        )
        units = list(db.scalars(select(DocumentUnit).where(DocumentUnit.file_id == entries[0].id)))
        assert len(units) == 2


def test_unit_pagination_and_file_membership(integrity_environment, project):
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    entry = detail["files"][0]
    url = f"/api/v1/source-manifests/{created['manifest_id']}/units?file_id={entry['id']}&roots_only=true&limit=1"
    result = client.get(url).json()
    assert result["has_more"] is True and len(result["items"]) == 1
    assert client.get(url + "&offset=1").json()["items"][0]["native_text"] == "不得提神。"
    assert client.get(url.replace(entry["id"], str(uuid.uuid4()))).status_code == 404


def test_missing_manifest_file_is_explicitly_blocked(integrity_environment, project):
    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    with sessions() as db:
        entry = db.scalar(
            select(SourceManifestFile).where(SourceManifestFile.manifest_id == created["manifest_id"])
        )
        db.delete(entry)
        db.commit()
    execute_run(created["run_id"])
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["report"]["processing_complete"] is False
    assert len(result["report"]["missing_asset_ids"]) == 1
    assert result["report"]["issues"][0]["code"] == "MANIFEST_FILES_MISSING"


def test_missing_child_region_breaks_reconciliation(integrity_environment, project):
    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    with sessions() as db:
        entry = db.scalar(
            select(SourceManifestFile).where(SourceManifestFile.manifest_id == created["manifest_id"])
        )
        root = db.scalar(select(DocumentUnit).where(DocumentUnit.file_id == entry.id))
        child = DocumentUnit(
            file_id=entry.id,
            project_id=entry.project_id,
            unit_key="test:child",
            parent_key=root.unit_key,
            position=3,
            kind="text_region",
            locator={},
            content_hash="a" * 64,
            state="CANDIDATE",
        )
        db.add(child)
        entry.inventory = {
            **entry.inventory,
            "detected_unit_keys": [*entry.inventory["detected_unit_keys"], "test:child"],
        }
        db.commit()
        db.delete(child)
        db.commit()
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["report"]["processing_complete"] is False
    assert result["files"][0]["report"]["missing_roots"] == []
    assert result["files"][0]["report"]["missing_detected_units"] == ["test:child"]


def test_memory_budget_failure_is_not_a_completed_source(integrity_environment, project, monkeypatch):
    from app.services.document_inventory import policy

    frozen = policy()
    frozen["reader_limits"]["memory_bytes"] = 1
    monkeypatch.setattr("app.api.integrity.policy", lambda: frozen)
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["status"] == "BLOCKED"
    assert result["report"]["processing_complete"] is False
    assert result["files"][0]["report"]["issues"][0]["code"] == "READER_MEMORY_BUDGET"


def test_original_image_has_authentication_and_hash_guard(integrity_environment, project):
    import fitz
    from app.services.storage import local_path

    client, sessions = integrity_environment
    document = fitz.open()
    document.new_page().insert_text((40, 40), "Do not promise")
    created, _ = create_manifest(
        client, project, create_source(client, project, "one.pdf", document.tobytes())
    )
    execute_run(created["run_id"])
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    fid = detail["files"][0]["id"]
    root = client.get(
        f"/api/v1/source-manifests/{created['manifest_id']}/units?file_id={fid}&roots_only=true"
    ).json()["items"][0]
    assert root["spans"][0]["unit_hash"] == root["content_hash"]
    url = f"/api/v1/source-manifests/{created['manifest_id']}/units/{root['id']}/artifacts/image"
    assert client.get(url).status_code == 200
    path = local_path(f"integrity/{created['manifest_id']}/{fid}/{root['artifacts']['image']}")
    path.write_bytes(b"changed bytes")
    assert client.get(url).status_code == 409
    client.delete("/api/v1/session")
    assert client.get(url).status_code == 401


def test_project_capacity_is_checked_before_region_reading(integrity_environment, project, monkeypatch):
    import fitz
    from app.services.document_inventory import policy

    frozen = policy()
    frozen["p0"]["max_project_pdf_pages"] = 0
    monkeypatch.setattr("app.api.integrity.policy", lambda: frozen)
    monkeypatch.setattr(
        "app.services.document_inventory.inspect_file",
        lambda *a: pytest.fail("Project capacity must block before OCR"),
    )
    client, _ = integrity_environment
    document = fitz.open()
    document.new_page()
    created, _ = create_manifest(
        client, project, create_source(client, project, "one.pdf", document.tobytes())
    )
    execute_run(created["run_id"])
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["status"] == "FAILED"
    assert result["report"]["processing_complete"] is False
    assert result["report"]["processed_roots"] == 0
    assert "项目页数超过预算" in result["report"]["limits"]


def test_reader_deadline_never_creates_a_completed_source(integrity_environment, project, monkeypatch):
    from app.services.document_inventory import policy

    frozen = policy()
    frozen["reader_limits"]["file_timeout_seconds"] = 0
    monkeypatch.setattr("app.api.integrity.policy", lambda: frozen)
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    result = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    assert result["status"] == "BLOCKED"
    assert result["report"]["processing_complete"] is False
    assert result["files"][0]["report"]["issues"][0]["code"] == "READER_TIMEOUT"


def test_markdown_auto_archive_download_and_tamper_guard(integrity_environment, project):
    from app.services.storage import local_path

    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    file = detail["files"][0]
    assert file["report"]["markdown"]["audit"]["status"] == "ALIGNED"
    assert [r["id"] for r in file["report"]["validation_rounds"]] == ["structure", "reading", "markdown"]
    url = f"/api/v1/source-manifests/{created['manifest_id']}/files/{file['id']}/markdown"
    result = client.get(url)
    assert result.status_code == 200 and "不得提神。" in result.text
    assert result.headers["content-disposition"].startswith("attachment;")
    assert client.post(url).json()["sha256"] == file["report"]["markdown"]["sha256"]
    local_path(f"integrity/{created['manifest_id']}/{file['id']}/source.md").write_text("missing content")
    assert client.get(url).status_code == 409
    assert client.post(url).status_code == 422
    client.delete("/api/v1/session")
    assert client.get(url).status_code == 401


def test_unfinished_read_cannot_be_exported_as_a_complete_md(integrity_environment, project):
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    detail = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()
    fid = detail["files"][0]["id"]
    url = f"/api/v1/source-manifests/{created['manifest_id']}/files/{fid}/markdown"
    assert client.post(url).status_code == 409
    assert client.get(url).status_code == 409


def test_source_reading_does_not_accept_business_approval(integrity_environment, project):
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    run = client.get(f"/api/v1/runs/{created['run_id']}").json()
    result = client.post(
        f"/api/v1/runs/{created['run_id']}/decisions",
        json={
            "expected_revision": run["revision"],
            "expected_input_hash": run["input_hash"],
            "decision": "APPROVE",
            "mode": "independent_human",
            "reason": "must not qualify source reading",
        },
    )
    assert result.status_code == 409 and result.json()["code"] == "NOT_REVIEW_RUN"


def test_rule_edit_does_not_invalidate_immutable_source_archive(integrity_environment, project):
    from app.models import ReviewRun
    from app.services.versioning import invalidate_runs

    client, sessions = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    with sessions() as db:
        invalidate_runs(db, project["id"])
        db.commit()
        assert db.get(ReviewRun, created["run_id"]).freshness == "CURRENT"


def test_live_source_units_and_spans_match_frozen_contract_fields(integrity_environment, project):
    import json

    from app.settings import ROOT

    definitions = json.loads((ROOT / "docs/contracts/source_integrity_v1.json").read_text())["$defs"]
    client, _ = integrity_environment
    created, _ = create_manifest(client, project, create_source(client, project))
    execute_run(created["run_id"])
    source = client.get(f"/api/v1/source-manifests/{created['manifest_id']}").json()["files"][0]
    assert set(source) == set(definitions["Source"]["required"])
    unit = client.get(
        f"/api/v1/source-manifests/{created['manifest_id']}/units?file_id={source['id']}"
    ).json()["items"][0]
    assert set(unit) == set(definitions["DocumentUnit"]["required"])
    for span in unit["spans"]:
        assert set(span) == set(definitions["SourceSpan"]["required"])
        assert span["unit_id"] == unit["id"] and span["unit_hash"] == unit["content_hash"]
        source_text = unit["native_text"] if span["channel"] == "native" else unit["ocr_text"]
        assert source_text[span["start"] : span["end"]] == source_text
