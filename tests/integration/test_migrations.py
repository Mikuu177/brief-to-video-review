"""Exercise the real migration chain against an empty isolated PostgreSQL schema."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from app import db, models
from sqlalchemy import create_engine, inspect, text


def test_empty_database_upgrade_and_repeated_startup_preserve_rows(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://creator:review_test_only@127.0.0.1:55433/creator_review_test")
    admin = create_engine(url)
    schema = "migration_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    isolated = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(db, "engine", isolated)
    config = Config(str(Path(__file__).resolve().parents[2] / "apps/api/alembic.ini"))
    try:
        command.upgrade(config, "0001")
        later = {"source_manifests", "source_manifest_files", "document_units", "video_content_snapshots"}
        assert not later.intersection(inspect(isolated).get_table_names())
        with isolated.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users(id,username,password_hash,role,created_at) VALUES (:id,'migration-survivor','test-only','admin',CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid4())},
            )
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert set(inspect(isolated).get_table_names()) == set(models.Base.metadata.tables) | {
            "alembic_version"
        }
        with isolated.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
            assert (
                connection.scalar(text("SELECT count(*) FROM users WHERE username='migration-survivor'")) == 1
            )
        command.downgrade(config, "base")
        assert inspect(isolated).get_table_names() == ["alembic_version"]
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
