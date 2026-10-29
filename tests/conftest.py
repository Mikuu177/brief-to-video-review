import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from app.api.auth import password_hash
from app.db import Base, get_db
from app.main import app
from app.models import User
from app.settings import settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "session")
    monkeypatch.setattr(settings, "session_secret", "test-only-session-secret-32-characters")
    # Separate PostgreSQL schema per test; no access to real project rows.
    engine = create_engine(os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://creator:review_test_only@127.0.0.1:55433/creator_review_test"))
    schema = "test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(isolated)
    sessions = sessionmaker(isolated, expire_on_commit=False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "")
    monkeypatch.setattr(settings, "ark_text_model", "")
    from app.workers import review

    monkeypatch.setattr(review, "SessionLocal", sessions)
    for module in ("assets", "rulesets", "reviews"):
        monkeypatch.setattr(f"app.api.{module}.enqueue", lambda run_id: None)
    monkeypatch.setattr(settings, "redis_url", os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56380/0"))
    from redis import Redis

    Redis.from_url(settings.redis_url).delete("login-attempts:testclient")

    def db_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with sessions() as db:
        db.add(User(username="test-admin", password_hash=password_hash("test-password"), role="admin"))
        db.add(
            User(username="test-reviewer", password_hash=password_hash("review-password"), role="reviewer")
        )
        db.commit()
    with TestClient(app, base_url="http://testserver") as client:
        assert (
            client.post(
                "/api/v1/sessions", json={"username": "test-admin", "password": "test-password"}
            ).status_code
            == 200
        )
        yield client, sessions
    app.dependency_overrides.clear()
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    engine.dispose()


@pytest.fixture
def project(environment):
    client, sessions = environment
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "隔离测试项目",
            "brand": "TestBrand",
            "product": "TestDrink",
            "platform": "xiaohongshu",
            "phase": "launch",
            "mode": "trial",
        },
    )
    assert response.status_code == 201
    return response.json()


def upload_text(client, project_id, kind="brief", content="不得声称喝后立即清醒。", name="test.txt"):
    r = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data={"kind": kind},
        files={"file": (name, content.encode(), "text/plain")},
    )
    assert r.status_code == 201, r.text
    asset = r.json()
    run = client.post(f"/api/v1/assets/{asset['id']}/parse").json()
    from app.workers.review import execute_run

    execute_run(run["run_id"])
    return client.get(f"/api/v1/assets/{asset['id']}/blocks").json()


def rule_for(block_id, quote="不得声称喝后立即清醒。", id="R-001"):
    return {
        "id": id,
        "title": "禁止功效暗示",
        "type": "must",
        "severity": "blocker",
        "scope": {"platforms": ["xiaohongshu"], "phases": ["launch"], "asset_kinds": ["script", "video"]},
        "predicate": {
            "kind": "text_semantic",
            "instruction": "禁止功效暗示",
            "terms": [],
            "window_ms": None,
            "manual_required": False,
        },
        "source_refs": [{"block_id": block_id, "quote": quote}],
        "exceptions": [],
        "relations": [],
        "confirmation_status": "confirmed",
        "confirmation_note": "",
    }


def frozen_rules(client, project_id):
    source = upload_text(client, project_id)
    ruleset = client.post(
        f"/api/v1/projects/{project_id}/rulesets/manual",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    patched = client.patch(
        f"/api/v1/rulesets/{ruleset['id']}",
        json={"expected_revision": 1, "rules": [rule_for(source["blocks"][0]["id"])], "conflicts": []},
    ).json()
    covered = client.post(
        f"/api/v1/rulesets/{ruleset['id']}/manual-coverage",
        json={
            "expected_revision": patched["revision"],
            "note": "隔离测试素材仅一行，人工声明已检查所有来源。",
        },
    ).json()
    response = client.post(
        f"/api/v1/rulesets/{ruleset['id']}/confirm",
        json={
            "expected_revision": covered["revision"],
            "note": "仅用于确定性测试，不是品牌确认。",
            "kind": "experimental",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sample_video(tmp_path):
    path = tmp_path / "synthetic-video.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=160x96:rate=30:duration=2", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=16000:duration=2", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)
    ], check=True, capture_output=True, timeout=30)
    return path
