import copy
from datetime import UTC, datetime

from app.api.auth import current_user, project_access
from app.db import get_db
from app.errors import fail
from app.models import Asset, ReviewRun, ReviewStage, User, VideoContentSnapshot
from app.schemas.video_content import (
    VERSION,
    ContentBatch,
    ContentConfirmation,
    ContentCreate,
    ContentRevision,
)
from app.services.content_binding import next_revision_id
from app.services.content_checks import word_coverage_checks
from app.services.runs import config_snapshot, enqueue
from app.services.versioning import content_hash
from app.services.video_content import content_report
from app.settings import settings
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


def access(db, snapshot_id, user):
    snapshot = db.get(VideoContentSnapshot, snapshot_id)
    if not snapshot:
        fail("NOT_FOUND", "内容快照不存在", 404)
    project_access(db, snapshot.project_id, user)
    return snapshot


def snapshot_dict(db, snapshot, include_payload=True):
    run = db.get(ReviewRun, snapshot.run_id) if snapshot.run_id else None
    origin, seen = snapshot, set()
    while not origin.run_id and origin.parent_id and origin.id not in seen:
        seen.add(origin.id)
        parent = db.get(VideoContentSnapshot, origin.parent_id)
        if not parent:
            break
        origin = parent
    execution_run = db.get(ReviewRun, origin.run_id) if origin.run_id else None
    superseded_by = next_revision_id(db, snapshot.id)
    asr_stage = (
        db.scalar(
            select(ReviewStage).where(
                ReviewStage.run_id == snapshot.run_id,
                ReviewStage.name == "content:asr_speakers_words",
                ReviewStage.status == "COMPLETED",
            )
        )
        if snapshot.run_id
        else None
    )
    return {
        "id": snapshot.id,
        "project_id": snapshot.project_id,
        "asset_id": snapshot.asset_id,
        "filename": db.get(Asset, snapshot.asset_id).filename,
        "source_sha256": snapshot.source_sha256,
        "config": snapshot.config,
        "execution_versions": execution_run.input_snapshot.get("config") if execution_run else None,
        "parent_id": snapshot.parent_id,
        "superseded_by": superseded_by,
        "asr_capabilities": asr_stage.output.get("capabilities") if asr_stage else None,
        "created_at": snapshot.created_at,
        "status": snapshot.status,
        "content_hash": snapshot.content_hash,
        "run_id": snapshot.run_id,
        "run_status": run.status if run else None,
        "error": run.error if run else None,
        "report": content_report(snapshot.payload),
        "payload": snapshot.payload if include_payload else None,
        "correction_log": snapshot.correction_log,
        "confirmation": snapshot.confirmation,
        "additional_checks": word_coverage_checks(snapshot)
        if include_payload and snapshot.content_hash
        else None,
        "production_ready": False,
        "stages": [
            {"name": item.name, "status": item.status, "attempt": item.attempt, "error": item.error}
            for item in db.scalars(
                select(ReviewStage)
                .where(ReviewStage.run_id == snapshot.run_id)
                .order_by(ReviewStage.created_at)
            )
        ]
        if include_payload and snapshot.run_id
        else [],
    }


@router.get("/projects/{project_id}/video-contents")
def listing(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project_access(db, project_id, user)
    rows = db.scalars(
        select(VideoContentSnapshot)
        .where(VideoContentSnapshot.project_id == project_id)
        .order_by(VideoContentSnapshot.created_at.desc())
        .limit(50)
    )
    return {"enabled": settings.video_content_enabled, "items": [snapshot_dict(db, s, False) for s in rows]}


@router.post("/projects/{project_id}/video-contents", status_code=202)
def create(
    project_id: str, body: ContentCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    project = project_access(db, project_id, user, lock=True)
    if not settings.video_content_enabled:
        fail("CONTENT_DISABLED", "内容读取预览未启用", 409)
    if project.mode != "trial" or not body.acknowledge_trial:
        fail("TRIAL_ONLY", "当前内容重建仅用于明确确认的技术试验项目", 409)
    asset = db.get(Asset, body.asset_id)
    if not asset or asset.project_id != project_id or asset.kind != "video":
        fail("ASSET_MISMATCH", "必须选择本项目完整视频")
    missing = [k for k in settings.missing("video") if k != "ARK_VIDEO_AUDIO_VERIFIED"]
    if missing:
        fail("MODEL_NOT_CONFIGURED", "视频/ASR服务配置不全", 409, missing=missing)
    prior = db.scalar(
        select(VideoContentSnapshot).where(
            VideoContentSnapshot.project_id == project_id,
            VideoContentSnapshot.idempotency_key == body.idempotency_key,
        )
    )
    if prior:
        if prior.asset_id != asset.id:
            fail("IDEMPOTENCY_CONFLICT", "提交键已用于其他原片", 409)
        return {"id": prior.id, "run_id": prior.run_id}
    config = {
        "version": VERSION,
        "schema_hash": content_hash(ContentBatch.model_json_schema()),
        "reading_profile": {
            "version": 2,
            "fps": 5,
            "thinking": {"AF": "disabled", "A": "disabled", "S": "enabled", "B": "disabled", "G": "enabled"},
            "bbox": "unsupported_null_only",
            "whole_read": "sentence_and_action_not_word",
        },
        "source_sha": asset.sha256,
        "target": {"brand": project.brand, "product": project.product},
        "limits": {"model_calls": 40, "max_ms": 300000, "events": 2000, "entities": 100},
    }
    snapshot = VideoContentSnapshot(
        project_id=project_id,
        asset_id=asset.id,
        source_sha256=asset.sha256,
        created_by=user.id,
        idempotency_key=body.idempotency_key,
        input_hash=content_hash(config),
        config=config,
    )
    db.add(snapshot)
    db.flush()
    run = ReviewRun(
        project_id=project_id,
        kind="video_content",
        input_snapshot={
            "content_snapshot_id": snapshot.id,
            "config": config_snapshot(),
            "allow_unverified_audio": True,
            "mode": "live",
            "trial_limits": ["内容层技术试验；事实和识别待人工核对，不是品牌或拍摄稿批准"],
        },
        input_hash=content_hash({"content_snapshot_id": snapshot.id, "config": config}),
        idempotency_key="content-" + snapshot.id,
    )
    db.add(run)
    db.flush()
    snapshot.run_id = run.id
    db.commit()
    try:
        enqueue(run.id)
    except Exception:
        run.error = {"code": "QUEUE_UNAVAILABLE", "message": "快照已登记，等待队列恢复"}
        db.commit()
    return {"id": snapshot.id, "run_id": run.id}


@router.get("/video-contents/{snapshot_id}")
def detail(snapshot_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return snapshot_dict(db, access(db, snapshot_id, user))


@router.post("/video-contents/{snapshot_id}/revisions", status_code=201)
def revise(
    snapshot_id: str, body: ContentRevision, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    source = access(db, snapshot_id, user)
    project_access(db, source.project_id, user, lock=True)
    if (
        source.content_hash != body.expected_content_hash
        or not content_report(source.payload)["processing_complete"]
    ):
        fail("VERSION_CONFLICT", "内容版本尚未完成或已变化", 409)
    payload = copy.deepcopy(source.payload)
    by_id = {e["id"]: e for e in payload["events"]}
    if next_revision_id(db, source.id):
        fail("VERSION_CONFLICT", "已有修正版本，请选择最新内容版本", 409)
    changes = []
    entities = {e["id"] for e in payload["entities"]}
    for correction in body.corrections:
        event = by_id.get(correction.event_id)
        if not event:
            fail("CONTENT_REFERENCE_INVALID", "待修正事件不在当前快照")
        fields = correction.model_dump(exclude_unset=True, exclude={"event_id"})
        before = copy.deepcopy(event)
        event.update(fields)
        if not event["raw_content"] or not 0 <= event["start_ms"] < event["end_ms"] <= payload["duration_ms"]:
            fail("CONTENT_TIME_INVALID", "修正文字或时间无效")
        for key in ["actor_id", "object_id"]:
            if event.get(key) and event[key] not in entities:
                fail("CONTENT_REFERENCE_INVALID", "修正主体/物品ID不存在")
        event["unit_ids"] = [
            u["id"]
            for u in payload["units"]
            if event["start_ms"] < u["end_ms"] and event["end_ms"] > u["start_ms"]
        ]
        changes.append(
            {
                "event_id": event["id"],
                "before": before,
                "after": copy.deepcopy(event),
                "reason": body.reason,
                "actor_id": user.id,
                "corrected_at": datetime.now(UTC).isoformat(),
            }
        )
    changed = {c["event_id"] for c in changes}
    for entity in payload["entities"]:
        if set(entity["evidence_ids"]) & changed:
            entity["status"] = "UNKNOWN"
            entity["requires_recheck"] = True
    for claim in payload["claims"]:
        if set(claim["evidence_ids"]) & changed:
            claim["status"] = "UNKNOWN"
            claim["requires_recheck"] = True
    payload["gaps"].append(
        {
            "code": "HUMAN_REVISION_RECHECK",
            "reason": "内容有修正，依赖解释/审核需重新检查",
            "event_ids": list(changed),
        }
    )
    snapshot = VideoContentSnapshot(
        project_id=source.project_id,
        asset_id=source.asset_id,
        source_sha256=source.source_sha256,
        created_by=user.id,
        parent_id=source.id,
        idempotency_key="revision-" + __import__("uuid").uuid4().hex,
        input_hash=source.input_hash,
        config=source.config,
        status="NEEDS_HUMAN",
        payload=payload,
        content_hash=content_hash(payload),
        correction_log=source.correction_log + changes,
    )
    db.add(snapshot)
    for run in db.scalars(
        select(ReviewRun).where(
            ReviewRun.project_id == source.project_id,
            ReviewRun.kind == "video",
            ReviewRun.freshness == "CURRENT",
            ReviewRun.input_snapshot["content_snapshot_id"].as_string() == source.id,
        )
    ):
        run.freshness = "STALE"
        run.revision += 1
    db.commit()
    return snapshot_dict(db, snapshot)


@router.post("/video-contents/{snapshot_id}/confirmation")
def confirm(
    snapshot_id: str,
    body: ContentConfirmation,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    source = access(db, snapshot_id, user)
    project_access(db, source.project_id, user, lock=True)
    if (
        source.content_hash != body.expected_content_hash
        or not content_report(source.payload)["processing_complete"]
    ):
        fail("VERSION_CONFLICT", "内容版本未完成或已变化", 409)
    if next_revision_id(db, source.id):
        fail("VERSION_CONFLICT", "此内容已有修正，请先检查最新版本", 409)
    if source.confirmation:
        fail("ALREADY_CONFIRMED", "该快照已记录内容确认", 409)
    if not body.acknowledge_remaining_gaps:
        fail("CONTENT_GAPS", "需明确已回放且了解剩余未决与识别限制", 409)
    checks = word_coverage_checks(source)
    if body.expected_checks_hash and body.expected_checks_hash != checks["hash"]:
        fail("VERSION_CONFLICT", "程序对账版本已变化，请重新查看待核对项", 409)
    source.confirmation = {
        "confirmed_at": datetime.now(UTC).isoformat(),
        "actor_id": user.id,
        "reason": body.reason,
        "remaining_gaps": len(source.payload.get("gaps", [])) + len(checks["gaps"]),
        "additional_checks_hash": checks["hash"],
        "additional_checks_version": checks["version"],
        "scope": "content review only; not gold labels or brand approval",
    }
    source.status = "HUMAN_REVIEWED_WITH_LIMITS"
    db.commit()
    return snapshot_dict(db, source)


@router.get("/video-contents/{snapshot_id}/archive")
def archive(snapshot_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    source = access(db, snapshot_id, user)
    if not source.content_hash:
        fail("CONTENT_NOT_READY", "内容未完成", 409)
    import json

    body = json.dumps(snapshot_dict(db, source), ensure_ascii=False, indent=2, default=str)
    return Response(
        body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="video-content-{source.id}.json"',
            "Cache-Control": "private, no-store",
        },
    )


class VisualCreate(ContentCreate):
    expected_content_hash: str


@router.get("/video-contents/{snapshot_id}/visual-inspections")
def visual_list(snapshot_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    source = access(db, snapshot_id, user)
    runs = db.scalars(
        select(ReviewRun)
        .where(
            ReviewRun.project_id == source.project_id,
            ReviewRun.kind == "visual_inspection",
            ReviewRun.input_snapshot["content_snapshot_id"].as_string() == source.id,
        )
        .order_by(ReviewRun.created_at.desc())
        .limit(10)
    )
    return {
        "items": [
            {"id": r.id, "status": r.status, "result": r.result, "error": r.error, "created_at": r.created_at}
            for r in runs
        ]
    }


@router.post("/video-contents/{snapshot_id}/visual-inspections", status_code=202)
def visual_create(
    snapshot_id: str, body: VisualCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    from app.errors import ReviewError
    from app.models import RuleSet
    from app.services.content_binding import bound_content

    source = access(db, snapshot_id, user)
    project = project_access(db, source.project_id, user, lock=True)
    if project.mode != "trial" or not body.acknowledge_trial:
        fail("TRIAL_ONLY", "专项诊断仅限技术试审", 409)
    if not settings.video_content_enabled:
        fail("CONTENT_DISABLED", "内容预览未启用", 409)
    if body.asset_id != source.asset_id:
        fail("ASSET_MISMATCH", "必须是内容快照的同一原片", 409)
    try:
        bound_content(db, source.id, db.get(Asset, source.asset_id), body.expected_content_hash)
    except ReviewError as exc:
        fail(exc.code, str(exc), 409)
    missing = [k for k in settings.missing("video") if k != "ARK_VIDEO_AUDIO_VERIFIED"]
    if missing:
        fail("MODEL_NOT_CONFIGURED", "视频服务配置不全", 409, missing=missing)
    prior = db.scalar(
        select(ReviewRun).where(
            ReviewRun.project_id == project.id, ReviewRun.idempotency_key == body.idempotency_key
        )
    )
    if prior:
        if prior.kind != "visual_inspection" or prior.input_snapshot.get("content_snapshot_id") != source.id:
            fail("IDEMPOTENCY_CONFLICT", "提交键已用于其他任务", 409)
        return {"run_id": prior.id}
    ruleset = db.get(RuleSet, project.current_ruleset_id) if project.current_ruleset_id else None
    from app.services.aggregate import applicable

    rules = (
        [r for r in ruleset.rules if applicable(r, project.platform, project.phase, "video")]
        if ruleset
        else []
    )
    from app.services.visual_inspection import METHOD_VERSION

    frozen = {
        "method_version": METHOD_VERSION,
        "ruleset_id": ruleset.id if ruleset else None,
        "ruleset_hash": ruleset.content_hash if ruleset else None,
        "asset_id": source.asset_id,
        "content_snapshot_id": source.id,
        "content_hash": source.content_hash,
        "target": {"brand": project.brand, "product": project.product},
        "video_rules": rules,
        "config": config_snapshot(),
        "allow_unverified_audio": True,
        "trial_limits": ["专项诊断不是品牌批准；参考图与品牌样例未校准"],
    }
    run = ReviewRun(
        project_id=project.id,
        kind="visual_inspection",
        input_snapshot=frozen,
        input_hash=content_hash(frozen),
        idempotency_key=body.idempotency_key,
        status="QUEUED",
    )
    db.add(run)
    db.commit()
    try:
        enqueue(run.id)
    except Exception:
        run.error = {"code": "QUEUE_UNAVAILABLE", "message": "任务保留，等待队列恢复"}
        db.commit()
    return {"run_id": run.id}
