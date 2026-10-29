from app.api.auth import current_user, project_access
from app.db import get_db
from app.errors import ReviewError, fail
from app.models import (
    Asset,
    HumanDecision,
    Observation,
    ReviewRun,
    ReviewStage,
    RuleCheck,
    RuleSet,
    ScriptApproval,
    Submission,
    User,
)
from app.schemas.contracts import DecisionInput, ReviewInput
from app.services.aggregate import validate_rule_scopes
from app.services.content_binding import bound_content
from app.services.content_checks import word_coverage_checks
from app.services.reports import render_report
from app.services.rulesets import extraction_target_current, source_limits
from app.services.runs import config_snapshot, enqueue
from app.services.versioning import content_hash, ensure_current, snapshot_submission
from app.settings import settings
from app.workers.review import observation_dict
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


def run_access(db, run_id, user, lock=False):
    query = select(ReviewRun).where(ReviewRun.id == run_id)
    run = db.scalar(query.with_for_update() if lock else query)
    if not run:
        fail("NOT_FOUND", "审核记录不存在", 404)
    project_access(db, run.project_id, user, lock=lock)
    return run


def create_review(kind, body, db, user, independent=False):
    submission = db.get(Submission, body.submission_id)
    if not submission:
        fail("NOT_FOUND", "提交不存在", 404)
    project = project_access(db, submission.project_id, user, lock=True)
    existing = db.scalar(
        select(ReviewRun).where(
            ReviewRun.project_id == project.id, ReviewRun.idempotency_key == body.idempotency_key
        )
    )
    if existing:
        return {"run_id": existing.id}
    ruleset = db.get(RuleSet, body.ruleset_id)
    if not ruleset or ruleset.project_id != project.id or ruleset.status != "FROZEN":
        fail("RULESET_UNCONFIRMED", "需要同项目已冻结的规范", 409)
    if project.current_ruleset_id != ruleset.id:
        fail("VERSION_CONFLICT", "请选择当前有效规范", 409)
    validate_rule_scopes(ruleset.rules, project.phase)
    if project.mode == "formal" and ruleset.confirmation_kind != "brand_confirmed":
        fail("RULESET_UNCONFIRMED", "正式项目需要品牌确认规范，实验规范仅用于试审", 409)
    if body.allow_unverified_audio and (
        independent
        or kind != "video"
        or project.mode != "trial"
        or ruleset.confirmation_kind != "experimental"
        or not body.historical_trial
        or len(body.trial_note.strip()) < 10
    ):
        fail("TRIAL_ONLY", "未验收音画仅可用于有明确说明的历史成片技术试审", 409)
    latest = db.scalar(
        select(Submission)
        .where(Submission.lineage_id == submission.lineage_id)
        .order_by(Submission.version.desc())
        .limit(1)
    )
    if latest.id != submission.id:
        fail("VERSION_CONFLICT", "请审核最新提交版本", 409)
    required_id = submission.script_id if kind == "script" else submission.video_id
    if not required_id:
        fail("INPUT_MISSING", "缺少脚本" if kind == "script" else "缺少原始视频")
    if not independent:
        for asset_id in [
            submission.script_id,
            submission.video_id if kind == "video" else None,
            submission.caption_id if kind == "video" else None,
        ]:
            if asset_id and db.get(Asset, asset_id).parse_status not in ("COMPLETED", "PARTIAL"):
                fail("PARSE_INCOMPLETE", "请先解析提交材料")
        missing = settings.missing("video" if kind == "video" else "text")
        if body.allow_unverified_audio:
            missing = [key for key in missing if key != "ARK_VIDEO_AUDIO_VERIFIED"]
        if missing:
            fail("MODEL_NOT_CONFIGURED", "等待模型配置，尚未执行 AI 审核", 409, missing=missing)
    if kind == "video" and not body.historical_trial and not independent:
        approval = db.get(ScriptApproval, body.script_approval_id) if body.script_approval_id else None
        if not approval or approval.script_id != submission.script_id or approval.ruleset_id != ruleset.id:
            fail("SCRIPT_APPROVAL_REQUIRED", "正式成片审核需要对应版本的脚本批准")
        # A new video snapshot invalidates the previous overall review, but an unchanged
        # approved script asset remains approved against the same immutable ruleset.
        if not submission.pairing_confirmed:
            fail("VERSION_CONFLICT", "脚本与视频关联尚未确认", 409)
    content = None
    if body.content_snapshot_id:
        if kind != "video" or independent:
            fail("CONTENT_SNAPSHOT_MISMATCH", "内容快照仅供AI成片审核引用", 409)
        try:
            content = bound_content(db, body.content_snapshot_id, db.get(Asset, required_id))
        except ReviewError as exc:
            fail(exc.code, str(exc), 409)
        if project.mode == "formal":
            fail("CONTENT_QUALIFICATION_PENDING", "内容层尚未取得生产资格，仅供技术试审", 409)
    snapshot = {
        "content_snapshot_id": content.id if content else None,
        "content_hash": content.content_hash if content else None,
        "content_additional_checks": word_coverage_checks(content) if content else None,
        "submission": snapshot_submission(submission),
        "ruleset_hash": ruleset.content_hash,
        "scope": {"platform": project.platform, "phase": project.phase},
        "product_context": {"brand": project.brand, "product": project.product},
        "verification_context_version": 3,
        "config": config_snapshot(),
        "historical_trial": body.historical_trial,
        "script_approval_id": body.script_approval_id,
        "mode": "independent_human" if independent else "live",
        "trial_limits": source_limits(ruleset)
        + (content.payload.get("limits", []) if content else [])
        + (
            ["视频音画尚未人工验收；口播、产品名和时间定位须人工复核。"]
            if body.allow_unverified_audio
            else []
        ),
        "allow_unverified_audio": body.allow_unverified_audio,
        "trial_note": body.trial_note,
    }
    run = ReviewRun(
        project_id=project.id,
        submission_id=submission.id,
        ruleset_id=ruleset.id,
        kind=kind,
        input_snapshot=snapshot,
        input_hash=content_hash(snapshot),
        idempotency_key=body.idempotency_key,
        status="INCOMPLETE" if independent else "QUEUED",
        result={"mode": "independent_human", "limits": ["未执行 AI 审核；人工独立判断。"]}
        if independent
        else {},
    )
    db.add(run)
    db.commit()
    if not independent:
        try:
            enqueue(run.id)
        except Exception:
            run.error = {"code": "QUEUE_UNAVAILABLE", "message": "等待队列恢复"}
            db.commit()
    return {"run_id": run.id}


@router.post("/script-reviews", status_code=202)
def script_review(body: ReviewInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return create_review("script", body, db, user)


@router.post("/video-reviews", status_code=202)
def video_review(body: ReviewInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return create_review("video", body, db, user)


@router.post("/human-reviews", status_code=201)
def human_review(body: ReviewInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    submission = db.get(Submission, body.submission_id)
    if not submission:
        fail("NOT_FOUND", "提交不存在", 404)
    return create_review("video" if submission.video_id else "script", body, db, user, independent=True)


def run_detail(db, run):
    stages = list(
        db.scalars(select(ReviewStage).where(ReviewStage.run_id == run.id).order_by(ReviewStage.created_at))
    )
    decisions = list(
        db.scalars(
            select(HumanDecision).where(HumanDecision.run_id == run.id).order_by(HumanDecision.created_at)
        )
    )
    ruleset = db.get(RuleSet, run.ruleset_id) if run.ruleset_id else None
    submission = db.get(Submission, run.submission_id) if run.submission_id else None
    asset_ids = (
        [getattr(submission, f"{kind}_id") for kind in ("script", "video", "cover", "caption")]
        if submission
        else []
    )
    return {
        **{
            key: getattr(run, key)
            for key in (
                "id",
                "project_id",
                "submission_id",
                "ruleset_id",
                "kind",
                "input_hash",
                "input_snapshot",
                "status",
                "assessment",
                "freshness",
                "revision",
                "result",
                "error",
                "created_at",
                "completed_at",
                "cancel_requested",
            )
        },
        "rules": ruleset.rules if ruleset else [],
        "ruleset_confirmation_kind": ruleset.confirmation_kind if ruleset else None,
        "assets": [
            {
                "id": a.id,
                "kind": a.kind,
                "filename": a.filename,
                "sha256": a.sha256,
                "metadata": {k: v for k, v in a.metadata_json.items() if k != "raw_probe"},
            }
            for a in db.scalars(select(Asset).where(Asset.id.in_([i for i in asset_ids if i])))
        ],
        "stages": [
            {
                key: getattr(s, key)
                for key in (
                    "id",
                    "name",
                    "attempt",
                    "status",
                    "request_id",
                    "error",
                    "usage",
                    "started_at",
                    "finished_at",
                )
            }
            for s in stages
        ],
        "checks": [
            {
                key: getattr(c, key)
                for key in (
                    "rule_id",
                    "status",
                    "evidence_ids",
                    "explanation",
                    "suggestion",
                    "applicability_reason",
                    "limitations",
                )
            }
            for c in db.scalars(select(RuleCheck).where(RuleCheck.run_id == run.id))
        ],
        "evidence": [
            observation_dict(o) for o in db.scalars(select(Observation).where(Observation.run_id == run.id))
        ],
        "decisions": [
            {
                **{key: getattr(d, key) for key in ("id", "decision", "reason", "mode", "created_at")},
                "actor": db.get(User, d.actor_id).username,
            }
            for d in decisions
        ],
    }


@router.get("/runs/{run_id}")
def detail(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return run_detail(db, run_access(db, run_id, user))


@router.get("/runs/{run_id}/checks")
def checks(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return run_detail(db, run_access(db, run_id, user))["checks"]


@router.get("/runs/{run_id}/evidence")
def evidence(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return run_detail(db, run_access(db, run_id, user))["evidence"]


@router.post("/runs/{run_id}/decisions", status_code=201)
def decide(
    run_id: str, body: DecisionInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    run = run_access(db, run_id, user, lock=True)
    if run.kind in ("source_reading", "video_content", "visual_inspection"):
        fail("NOT_REVIEW_RUN", "来源读取记录不接受脚本或成片审核决定", 409)
    project = project_access(db, run.project_id, user, lock=True)
    ensure_current(db, project, run)
    if body.expected_input_hash != run.input_hash or body.expected_revision != run.revision:
        fail("VERSION_CONFLICT", "审核记录已变化，请刷新后重试", 409)
    if body.mode == "ai_assisted" and run.status != "COMPLETED":
        fail("REVIEW_INCOMPLETE", "AI 审核尚未完整完成；可明确选择人工独立审核", 409)
    if body.mode == "ai_assisted" and run.input_snapshot.get("mode") != "live":
        fail("REVIEW_INCOMPLETE", "此记录未执行真实 AI 审核", 409)
    if run.status in ("QUEUED", "RUNNING"):
        fail("REVIEW_INCOMPLETE", "任务仍在运行，请先等待或取消", 409)
    decision = HumanDecision(
        run_id=run.id,
        input_hash=run.input_hash,
        actor_id=user.id,
        decision=body.decision,
        reason=body.reason,
        mode=body.mode,
    )
    db.add(decision)
    run.revision += 1
    db.commit()
    return run_detail(db, run)


class ApprovalInput(BaseModel):
    run_id: str
    expected_input_hash: str
    expected_revision: int
    reason: str = Field(min_length=1)


@router.post("/script-approvals", status_code=201)
def approve_script(body: ApprovalInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = run_access(db, body.run_id, user, lock=True)
    project = project_access(db, run.project_id, user, lock=True)
    ensure_current(db, project, run)
    if run.input_hash != body.expected_input_hash or run.revision != body.expected_revision:
        fail("VERSION_CONFLICT", "脚本审核记录已变化", 409)
    if run.kind != "script" or run.status != "COMPLETED" or run.assessment == "NOT_READY":
        fail("REVIEW_INCOMPLETE", "需要已完整完成的脚本审核", 409)
    submission = db.get(Submission, run.submission_id)
    approval = ScriptApproval(
        run_id=run.id,
        script_id=submission.script_id,
        ruleset_id=run.ruleset_id,
        actor_id=user.id,
        decision="APPROVE",
        reason=body.reason,
    )
    db.add(approval)
    run.revision += 1
    db.commit()
    return {"id": approval.id, "script_id": approval.script_id, "ruleset_id": approval.ruleset_id}


class RetryInput(BaseModel):
    expected_revision: int
    acknowledge_unknown_remote: bool = False


@router.post("/runs/{run_id}/retry", status_code=202)
def retry(run_id: str, body: RetryInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = run_access(db, run_id, user, lock=True)
    if run.status not in ("INCOMPLETE", "FAILED", "CANCELED") or run.revision != body.expected_revision:
        fail("VERSION_CONFLICT", "当前任务不能重试或版本已变化", 409)
    if run.input_snapshot.get("mode") == "independent_human":
        fail("REVIEW_INCOMPLETE", "人工独立记录不能转换成 AI 任务，请新建审核", 409)
    if run.freshness == "STALE":
        fail("VERSION_CONFLICT", "旧版本任务需要重新创建", 409)
    if run.kind == "extract" and not extraction_target_current(db.get(RuleSet, run.ruleset_id), run):
        fail("VERSION_CONFLICT", "规范已被人工修改或冻结，请创建新的提取草案", 409)
    if run.error and run.error.get("unknown_remote_state") and not body.acknowledge_unknown_remote:
        fail("UNKNOWN_REMOTE_STATE", "上次远端状态不明，重试可能重复调用，需要明确确认", 409)
    if run.kind not in ("parse", "source_reading"):
        missing = settings.missing(
            "video" if run.kind in ("video", "video_content", "visual_inspection") else "text"
        )
        if run.kind in ("video", "video_content", "visual_inspection") and run.input_snapshot.get(
            "allow_unverified_audio"
        ):
            missing = [key for key in missing if key != "ARK_VIDEO_AUDIO_VERIFIED"]
        if missing:
            fail("MODEL_NOT_CONFIGURED", "等待模型配置", 409, missing=missing)
        if run.input_snapshot.get("config") != config_snapshot():
            fail("VERSION_CONFLICT", "模型或指令配置已变化，请新建审核", 409)
    run.status = "QUEUED"
    run.cancel_requested = False
    run.revision += 1
    db.commit()
    enqueue(run.id)
    return {"run_id": run.id}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = run_access(db, run_id, user, lock=True)
    if run.status not in ("QUEUED", "RUNNING"):
        return {"status": run.status}
    run.cancel_requested = True
    run.revision += 1
    if run.status == "QUEUED":
        run.status = "CANCELED"
    db.commit()
    return {
        "status": run.status,
        "cancel_requested": True,
        "message": "停止后续阶段；已发出的远端请求不保证已停止",
    }


@router.get("/runs/{run_id}/report")
def report(
    run_id: str, format: str = "html", db: Session = Depends(get_db), user: User = Depends(current_user)
):
    data = run_detail(db, run_access(db, run_id, user))
    if format == "json":
        return data
    if format != "html":
        fail("INVALID_FORMAT", "支持 html 或 json")
    return HTMLResponse(
        render_report(data),
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )
