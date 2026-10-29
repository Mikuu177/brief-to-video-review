from datetime import UTC, datetime

from app.api.auth import current_user, project_access
from app.api.projects import ruleset_dict
from app.db import get_db
from app.errors import fail
from app.models import Asset, ReviewRun, RuleSet, User
from app.schemas.contracts import ExtractInput, RulesConfirm, RulesPatch
from app.services.rulesets import validate_sources
from app.services.runs import config_snapshot, enqueue
from app.services.versioning import content_hash, invalidate_runs
from app.settings import settings
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


def ruleset_access(db, ruleset_id, user, lock=False):
    query = select(RuleSet).where(RuleSet.id == ruleset_id)
    ruleset = db.scalar(query.with_for_update() if lock else query)
    if not ruleset:
        fail("NOT_FOUND", "规范不存在", 404)
    project_access(db, ruleset.project_id, user, lock=lock)
    return ruleset


def source_assets(db, project_id, source_ids):
    result = []
    for source_id in source_ids:
        asset = db.get(Asset, source_id)
        if not asset or asset.project_id != project_id or asset.kind != "brief":
            fail("ASSET_MISMATCH", "Brief 不属于该项目")
        if asset.parse_status not in ("COMPLETED", "PARTIAL"):
            fail("PARSE_INCOMPLETE", "请先解析 Brief")
        result.append(asset)
    return result


@router.post("/projects/{project_id}/rulesets/extract", status_code=202)
def extract(
    project_id: str, body: ExtractInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    project = project_access(db, project_id, user, lock=True)
    existing = db.scalar(
        select(ReviewRun).where(
            ReviewRun.project_id == project.id, ReviewRun.idempotency_key == body.idempotency_key
        )
    )
    if existing:
        return {"run_id": existing.id, "ruleset_id": existing.ruleset_id}
    if missing := settings.missing("text"):
        fail("MODEL_NOT_CONFIGURED", "等待模型配置，尚未执行 AI 规范提取", 409, missing=missing)
    source_assets(db, project.id, body.source_ids)
    ruleset = RuleSet(project_id=project.id, source_ids=body.source_ids)
    db.add(ruleset)
    db.flush()
    snapshot = {
        "source_ids": body.source_ids,
        "ruleset_revision": ruleset.revision,
        "config": config_snapshot(),
        "scope": {"platform": project.platform, "phase": project.phase},
    }
    run = ReviewRun(
        project_id=project.id,
        ruleset_id=ruleset.id,
        kind="extract",
        input_snapshot=snapshot,
        input_hash=content_hash(snapshot),
        idempotency_key=body.idempotency_key,
    )
    db.add(run)
    db.commit()
    try:
        enqueue(run.id)
    except Exception:
        run.error = {"code": "QUEUE_UNAVAILABLE", "message": "等待队列恢复"}
        db.commit()
    return {"run_id": run.id, "ruleset_id": ruleset.id}


@router.post("/projects/{project_id}/rulesets/manual", status_code=201)
def manual(
    project_id: str, body: ExtractInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    project = project_access(db, project_id, user)
    source_assets(db, project.id, body.source_ids)
    ruleset = RuleSet(project_id=project.id, source_ids=body.source_ids)
    db.add(ruleset)
    db.commit()
    return ruleset_dict(ruleset)


@router.patch("/rulesets/{ruleset_id}")
def patch(
    ruleset_id: str, body: RulesPatch, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    ruleset = ruleset_access(db, ruleset_id, user, lock=True)
    if ruleset.revision != body.expected_revision:
        fail("VERSION_CONFLICT", "规范已被更新，请刷新后重试", 409)
    if ruleset.status == "FROZEN":
        ruleset = RuleSet(
            project_id=ruleset.project_id,
            source_ids=ruleset.source_ids,
            extraction_coverage=ruleset.extraction_coverage,
        )
        db.add(ruleset)
    else:
        ruleset.revision += 1
    ruleset.rules = [rule.model_dump() for rule in body.rules]
    ruleset.conflicts = [conflict.model_dump() for conflict in body.conflicts]
    db.commit()
    return ruleset_dict(ruleset)


class CoverageInput(BaseModel):
    expected_revision: int
    note: str = Field(min_length=10)


@router.post("/rulesets/{ruleset_id}/manual-coverage")
def manual_coverage(
    ruleset_id: str, body: CoverageInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    ruleset = ruleset_access(db, ruleset_id, user, lock=True)
    if ruleset.status != "DRAFT" or ruleset.revision != body.expected_revision:
        fail("VERSION_CONFLICT", "仅能更新当前草案的人工检查记录", 409)
    assets = list(db.scalars(select(Asset).where(Asset.id.in_(ruleset.source_ids))))
    # The explicit whole-source declaration covers visual content too. Preserve the
    # parser's raw limitations instead of relabeling an image as successfully OCR'd.
    for asset in assets:
        asset.metadata_json = {
            **asset.metadata_json,
            "human_coverage_ack": {
                "actor": user.username,
                "note": body.note,
                "source_sha256": asset.sha256,
                "declared_at": datetime.now(UTC).isoformat(),
                "scope": "full_source_including_visual_content",
            },
        }
    ruleset.extraction_coverage = [
        {
            "asset_id": asset.id,
            "unit": item.get("page", item.get("sheet", "document")),
            "status": "success",
            "method": "human_declared",
            "actor": user.username,
            "note": body.note,
        }
        for asset in assets
        for item in asset.coverage
    ]
    ruleset.revision += 1
    db.commit()
    return ruleset_dict(ruleset)


@router.post("/rulesets/{ruleset_id}/confirm")
def confirm(
    ruleset_id: str, body: RulesConfirm, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    ruleset = ruleset_access(db, ruleset_id, user, lock=True)
    if ruleset.status != "DRAFT" or ruleset.revision != body.expected_revision:
        fail("VERSION_CONFLICT", "草案版本已变化或已冻结", 409)
    project = project_access(db, ruleset.project_id, user, lock=True)
    if body.accept_partial_sources and (
        project.mode != "trial" or body.kind != "experimental" or len(body.note.strip()) < 10
    ):
        fail("TRIAL_ONLY", "读取限制仅可在试审项目的技术规范中明确接受，须记录限制说明", 409)
    validate_sources(db, ruleset, accept_partial_sources=body.accept_partial_sources)
    if body.kind == "brand_confirmed" and user.role != "admin":
        fail("FORBIDDEN", "品牌规范确认需要管理员权限", 403)
    if body.kind == "brand_confirmed" and (
        project.platform == "unconfirmed" or project.phase == "unconfirmed"
    ):
        fail("INPUT_MISSING", "品牌规范需要明确平台与阶段")
    ruleset.status = "FROZEN"
    ruleset.revision += 1
    ruleset.confirmed_by = user.id
    ruleset.confirmed_at = datetime.now(UTC)
    ruleset.confirmation_note = body.note
    ruleset.confirmation_kind = body.kind
    ruleset.content_hash = content_hash(
        {
            "rules": ruleset.rules,
            "sources": ruleset.source_ids,
            "conflicts": ruleset.conflicts,
            "kind": body.kind,
        }
    )
    invalidate_runs(db, project.id)
    project.current_ruleset_id = ruleset.id
    project.revision += 1
    db.commit()
    return ruleset_dict(ruleset)
