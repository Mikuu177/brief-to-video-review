"""Add separate source inventory/reading records; preserve legacy review data."""

from alembic import op
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def common():
    return [Column("id", String(36), primary_key=True), Column("created_at", DateTime(timezone=True))]


def upgrade():
    op.create_table(
        "source_manifests",
        *common(),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("created_by", String(36), ForeignKey("users.id"), nullable=False),
        Column("name", String(200), nullable=False),
        Column("idempotency_key", String(100), nullable=False),
        Column("input_hash", String(64), nullable=False),
        Column("config", JSON, nullable=False),
        Column("status", String(30), nullable=False),
        Column("report", JSON, nullable=False),
        Column("run_id", String(36), ForeignKey("review_runs.id")),
        UniqueConstraint("project_id", "idempotency_key"),
    )
    op.create_index("ix_source_manifests_project_id", "source_manifests", ["project_id"])
    op.create_table(
        "source_manifest_files",
        *common(),
        Column("manifest_id", String(36), ForeignKey("source_manifests.id"), nullable=False),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("asset_id", String(36), ForeignKey("assets.id"), nullable=False),
        Column("source_sha256", String(64), nullable=False),
        Column("role", String(30), nullable=False),
        Column("platform", String(30), nullable=False),
        Column("status", String(30), nullable=False),
        Column("inventory", JSON, nullable=False),
        Column("report", JSON, nullable=False),
        UniqueConstraint("manifest_id", "asset_id"),
    )
    for key in ["manifest_id", "project_id"]:
        op.create_index(f"ix_source_manifest_files_{key}", "source_manifest_files", [key])
    op.create_table(
        "document_units",
        *common(),
        Column("file_id", String(36), ForeignKey("source_manifest_files.id"), nullable=False),
        Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
        Column("unit_key", String(200), nullable=False),
        Column("parent_key", String(200)),
        Column("position", Integer, nullable=False),
        Column("kind", String(30), nullable=False),
        Column("locator", JSON, nullable=False),
        Column("native_text", Text, nullable=False),
        Column("ocr_text", Text, nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("state", String(30), nullable=False),
        Column("issues", JSON, nullable=False),
        Column("features", JSON, nullable=False),
        Column("artifacts", JSON, nullable=False),
        UniqueConstraint("file_id", "unit_key"),
    )
    for key in ["file_id", "project_id"]:
        op.create_index(f"ix_document_units_{key}", "document_units", [key])


def downgrade():
    op.drop_table("document_units")
    op.drop_table("source_manifest_files")
    op.drop_table("source_manifests")
