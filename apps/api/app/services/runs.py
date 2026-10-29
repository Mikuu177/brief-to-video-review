from datetime import UTC, datetime

from app.errors import ReviewError
from app.models import ReviewStage
from app.settings import ROOT, settings
from redis import Redis
from rq import Queue
from rq.timeouts import JobTimeoutException
from sqlalchemy import select


def enqueue(run_id: str):
    queue = Queue(settings.queue_name, connection=Redis.from_url(settings.redis_url))
    # Persisted run exists before this call; enqueue failure can be recovered.
    job_id = f"review-{run_id}"
    job = queue.fetch_job(job_id)
    if job and job.get_status(refresh=True).value in ("queued", "started", "deferred", "scheduled"):
        return
    if job:
        job.delete()
    queue.enqueue(
        "app.workers.review.execute_run",
        run_id,
        job_id=job_id,
        job_timeout=settings.review_job_timeout_seconds,
        result_ttl=86400,
        failure_ttl=604800,
    )


def stage(db, run, name: str, function):
    db.refresh(run)
    if run.cancel_requested:
        raise ReviewError("CANCELED", "已请求取消；已发出的供应商调用是否终止需以供应商为准")
    previous = db.scalar(
        select(ReviewStage)
        .where(ReviewStage.run_id == run.id, ReviewStage.name == name)
        .order_by(ReviewStage.attempt.desc())
        .limit(1)
    )
    if previous and previous.status == "COMPLETED":
        return previous.output
    item = ReviewStage(
        run_id=run.id,
        name=name,
        attempt=previous.attempt + 1 if previous else 1,
        input_hash=run.input_hash,
        status="RUNNING",
        started_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    try:
        output = function()
        # A cancellation can arrive while the provider is running. Serialize this
        # commit with cancellation and discard this stage's uncommitted writes.
        db.refresh(run, attribute_names=["cancel_requested", "revision"], with_for_update=True)
        if run.cancel_requested:
            raise ReviewError("CANCELED", "已请求取消，当前阶段结果不再提交；远端请求不保证已停止")
        item.output = output
        item.status = "COMPLETED"
        item.finished_at = datetime.now(UTC)
        if isinstance(output, dict):
            records = output.get("_provider_records", [])
            item.usage = {"provider_records": records, "asr_usage": output.get("usage", {})}
            item.request_id = output.get("request_id") or (records[-1].get("request_id") if records else None)
        db.commit()
        return output
    except Exception as exc:
        db.rollback()
        item = db.get(ReviewStage, item.id)
        item.status = "FAILED"
        item.finished_at = datetime.now(UTC)
        item.error = error_detail(exc)
        db.commit()
        raise


def error_detail(exc):
    if isinstance(exc, JobTimeoutException):
        return {
            "code": "WORKER_TIME_BUDGET_EXCEEDED",
            "message": "任务执行时限已到，已完成阶段保留；远端调用状态可能未知，请核对后重试",
            "unknown_remote_state": True,
        }
    if isinstance(exc, ReviewError):
        return {"code": exc.code, "message": str(exc), "unknown_remote_state": exc.unknown_remote}
    return {
        "code": "INTERNAL_ERROR",
        "message": "服务端处理失败；错误已记录，请查看阶段日志",
        "exception_type": type(exc).__name__,
    }


def prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text()


def prompt_versions() -> dict:
    from app.services.versioning import content_hash

    return {path.name: content_hash(path.read_text()) for path in sorted((ROOT / "prompts").glob("*.md"))}


def config_snapshot() -> dict:
    return {
        "video_model": settings.ark_video_model,
        "text_model": settings.ark_text_model,
        "preprocess_model": settings.ark_preprocess_model,
        "window_ms": settings.review_window_ms,
        "overlap_ms": settings.review_overlap_ms,
        "full_fps": settings.review_full_fps,
        "detail_fps": settings.review_detail_fps,
        "video_audio_verified": settings.ark_video_audio_verified,
        "live_evaluation_approved": settings.review_live_evaluation_approved,
        "asr_resource_id": settings.asr_resource_id,
        "schema_version": "0.1.0",
        "prompt_versions": prompt_versions(),
    }
