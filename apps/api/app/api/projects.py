from app.api.assets import asset_dict
from app.api.auth import current_user, project_access
from app.db import get_db
from app.errors import fail
from app.models import Asset, Project, ReviewRun, RuleSet, ScriptApproval, Submission, User, uid
from app.schemas.contracts import ProjectInput, SubmissionInput
from app.services.versioning import invalidate_runs
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


def project_dict(project):
    return {
        key: getattr(project, key)
        for key in (
            "id",
            "name",
            "brand",
            "product",
            "platform",
            "phase",
            "mode",
            "revision",
            "current_ruleset_id",
            "created_at",
        )
    }


def submission_dict(submission):
    return {
        key: getattr(submission, key)
        for key in (
            "id",
            "creator",
            "lineage_id",
            "parent_id",
            "version",
            "script_id",
            "video_id",
            "cover_id",
            "caption_id",
            "pairing_confirmed",
            "created_at",
        )
    }


def ruleset_dict(ruleset):
    return {
        key: getattr(ruleset, key)
        for key in (
            "id",
            "status",
            "source_ids",
            "rules",
            "conflicts",
            "revision",
            "extraction_coverage",
            "content_hash",
            "confirmed_by",
            "confirmed_at",
            "confirmation_kind",
            "confirmation_note",
            "created_at",
        )
    }


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Project).order_by(Project.created_at.desc()))
    return [
        project_dict(p)
        for p in rows
        if user.role == "admin" or user.id == p.owner_id or user.id in p.member_ids
    ]


@router.post("/projects", status_code=201)
def create(body: ProjectInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.mode == "formal" and (body.platform == "unconfirmed" or body.phase == "unconfirmed"):
        fail("INPUT_MISSING", "正式项目需要明确平台和阶段")
    project = Project(**body.model_dump(), owner_id=user.id)
    db.add(project)
    db.commit()
    return project_dict(project)


@router.get("/projects/{project_id}")
def detail(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    p = project_access(db, project_id, user)
    assets = list(db.scalars(select(Asset).where(Asset.project_id == p.id).order_by(Asset.created_at)))
    submissions = list(
        db.scalars(
            select(Submission).where(Submission.project_id == p.id).order_by(Submission.created_at.desc())
        )
    )
    return {
        "project": project_dict(p),
        "assets": [asset_dict(a) for a in assets],
        "rulesets": [
            ruleset_dict(r)
            for r in db.scalars(
                select(RuleSet).where(RuleSet.project_id == p.id).order_by(RuleSet.created_at.desc())
            )
        ],
        "submissions": [submission_dict(s) for s in submissions],
        "runs": [
            {
                **{
                    key: getattr(r, key)
                    for key in (
                        "id",
                        "kind",
                        "status",
                        "assessment",
                        "freshness",
                        "submission_id",
                        "ruleset_id",
                        "created_at",
                        "error",
                    )
                },
                "content_snapshot_id": r.input_snapshot.get("content_snapshot_id"),
            }
            for r in db.scalars(
                select(ReviewRun).where(ReviewRun.project_id == p.id).order_by(ReviewRun.created_at.desc())
            )
        ],
        "script_approvals": [
            {key: getattr(a, key) for key in ("id", "script_id", "ruleset_id", "decision", "created_at")}
            for a in db.scalars(
                select(ScriptApproval)
                .join(ReviewRun, ScriptApproval.run_id == ReviewRun.id)
                .where(ReviewRun.project_id == p.id)
            )
        ],
    }


@router.post("/projects/{project_id}/submissions", status_code=201)
def submit(
    project_id: str, body: SubmissionInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    project = project_access(db, project_id, user, lock=True)
    if not any((body.script_id, body.video_id, body.cover_id, body.caption_id)):
        fail("INPUT_MISSING", "至少选择一种提交素材")
    for kind in ("script", "video", "cover", "caption"):
        asset_id = getattr(body, f"{kind}_id")
        if asset_id:
            asset = db.get(Asset, asset_id)
            if not asset or asset.project_id != project.id or asset.kind != kind:
                fail("ASSET_MISMATCH", "素材类型或所属项目不匹配", asset_id=asset_id)
    parent = None
    if body.parent_id:
        parent = db.get(Submission, body.parent_id)
        if not parent or parent.project_id != project.id:
            fail("VERSION_CONFLICT", "父提交不存在", 409)
        latest = db.scalar(
            select(Submission)
            .where(Submission.lineage_id == parent.lineage_id)
            .order_by(Submission.version.desc())
            .limit(1)
        )
        if latest.id != parent.id:
            fail("VERSION_CONFLICT", "请从最新版本修订", 409)
        invalidate_runs(db, project.id, parent.lineage_id)
    submission = Submission(
        project_id=project.id,
        **body.model_dump(),
        lineage_id=parent.lineage_id if parent else uid(),
        version=parent.version + 1 if parent else 1,
    )
    db.add(submission)
    project.revision += 1
    db.commit()
    return submission_dict(submission)
