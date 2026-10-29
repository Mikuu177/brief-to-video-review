from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC)


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))


class LoginSession(Record, Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Record, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str] = mapped_column(String(200))
    product: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(30))
    phase: Mapped[str] = mapped_column(String(80))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    member_ids: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(30), default="trial")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_ruleset_id: Mapped[str | None] = mapped_column(String(36))


class Asset(Record, Base):
    __tablename__ = "assets"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    parse_status: Mapped[str] = mapped_column(String(30), default="NOT_PARSED")
    coverage: Mapped[list] = mapped_column(JSON, default=list)


class SourceBlock(Record, Base):
    __tablename__ = "source_blocks"
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    locator: Mapped[dict] = mapped_column(JSON)
    extraction_method: Mapped[str] = mapped_column(String(100))
    coverage_status: Mapped[str] = mapped_column(String(30))


class RuleSet(Record, Base):
    __tablename__ = "rulesets"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    source_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    extraction_coverage: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_note: Mapped[str | None] = mapped_column(Text)
    confirmation_kind: Mapped[str | None] = mapped_column(String(30))


class Submission(Record, Base):
    __tablename__ = "submissions"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    creator: Mapped[str] = mapped_column(String(200))
    lineage_id: Mapped[str] = mapped_column(String(36))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    script_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    video_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    cover_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    caption_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    pairing_confirmed: Mapped[bool] = mapped_column(default=False)


class ReviewRun(Record, Base):
    __tablename__ = "review_runs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    submission_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id"))
    ruleset_id: Mapped[str | None] = mapped_column(ForeignKey("rulesets.id"))
    kind: Mapped[str] = mapped_column(String(30))
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    assessment: Mapped[str] = mapped_column(String(30), default="NOT_READY")
    freshness: Mapped[str] = mapped_column(String(20), default="CURRENT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewStage(Record, Base):
    __tablename__ = "review_stages"
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    name: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    input_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict | list | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "name", "attempt"),)


class Observation(Record, Base):
    __tablename__ = "observations"
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    channel: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    uncertainty_ms: Mapped[int | None] = mapped_column(Integer)
    source_block_id: Mapped[str | None] = mapped_column(String(36))
    origin: Mapped[dict] = mapped_column(JSON)


class RuleCheck(Record, Base):
    __tablename__ = "rule_checks"
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    rule_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    applicability_reason: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("run_id", "rule_id"),)


class ScriptApproval(Record, Base):
    __tablename__ = "script_approvals"
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    script_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    ruleset_id: Mapped[str] = mapped_column(ForeignKey("rulesets.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)


class HumanDecision(Record, Base):
    __tablename__ = "human_decisions"
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    input_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)


class SourceManifest(Record, Base):
    __tablename__ = "source_manifests"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="INGESTED")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("review_runs.id"))
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)


class SourceManifestFile(Record, Base):
    __tablename__ = "source_manifest_files"
    manifest_id: Mapped[str] = mapped_column(ForeignKey("source_manifests.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(30))
    platform: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    inventory: Mapped[dict] = mapped_column(JSON, default=dict)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("manifest_id", "asset_id"),)


class DocumentUnit(Record, Base):
    __tablename__ = "document_units"
    file_id: Mapped[str] = mapped_column(ForeignKey("source_manifest_files.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    unit_key: Mapped[str] = mapped_column(String(200))
    parent_key: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    locator: Mapped[dict] = mapped_column(JSON)
    native_text: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30))
    issues: Mapped[list] = mapped_column(JSON, default=list)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("file_id", "unit_key"),)


class VideoContentSnapshot(Record, Base):
    __tablename__ = "video_content_snapshots"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("video_content_snapshots.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("review_runs.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="INGESTED")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    correction_log: Mapped[list] = mapped_column(JSON, default=list)
    confirmation: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"),)
