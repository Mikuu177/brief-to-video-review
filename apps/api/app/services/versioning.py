import hashlib
import json

from app.models import ReviewRun, Submission
from sqlalchemy import select


def content_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def invalidate_runs(db, project_id: str, lineage_id: str | None = None):
    query = select(ReviewRun).where(
        ReviewRun.project_id == project_id,
        ReviewRun.freshness == "CURRENT",
        ReviewRun.kind.not_in(("source_reading", "video_content", "visual_inspection")),
    )
    if lineage_id:
        ids = select(Submission.id).where(Submission.lineage_id == lineage_id)
        query = query.where(ReviewRun.submission_id.in_(ids))
    for run in db.scalars(query):
        run.freshness = "STALE"
        run.revision += 1


def ensure_current(db, project, run):
    from app.errors import fail

    if run.freshness != "CURRENT" or project.current_ruleset_id != run.ruleset_id:
        fail("VERSION_CONFLICT", "输入版本已变化，请重新审核", 409)
    submission = db.get(Submission, run.submission_id)
    latest = db.scalar(
        select(Submission)
        .where(Submission.lineage_id == submission.lineage_id)
        .order_by(Submission.version.desc())
        .limit(1)
    )
    if latest.id != submission.id:
        fail("VERSION_CONFLICT", "已有新的提交版本，请重新审核", 409)


def snapshot_submission(submission):
    return {
        key: getattr(submission, key)
        for key in (
            "id",
            "version",
            "lineage_id",
            "script_id",
            "video_id",
            "cover_id",
            "caption_id",
            "pairing_confirmed",
        )
    }
