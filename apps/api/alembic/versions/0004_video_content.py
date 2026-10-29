"""Immutable content snapshots alongside existing review results."""

from alembic import op
from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, UniqueConstraint

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_content_snapshots",
        Column("id", String(36), primary_key=True),
        Column("created_at", DateTime(timezone=True)),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("asset_id", String(36), ForeignKey("assets.id"), nullable=False),
        Column("source_sha256", String(64), nullable=False),
        Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
        Column("parent_id", String(36), ForeignKey("video_content_snapshots.id")),
        Column("run_id", String(36), ForeignKey("review_runs.id")),
        Column("idempotency_key", String(100), nullable=False),
        Column("input_hash", String(64), nullable=False),
        Column("config", JSON, nullable=False),
        Column("status", String(30), nullable=False),
        Column("payload", JSON, nullable=False),
        Column("content_hash", String(64)),
        Column("correction_log", JSON, nullable=False),
        Column("confirmation", JSON),
        UniqueConstraint("project_id", "idempotency_key"),
    )
    op.create_index("ix_video_content_snapshots_project_id", "video_content_snapshots", ["project_id"])


def downgrade():
    op.drop_table("video_content_snapshots")
