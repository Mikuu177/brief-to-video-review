"""Run periodically: recover queued jobs and mark abandoned calls incomplete, never auto-reissue unknown calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))

from app.db import SessionLocal
from app.models import ReviewRun, ReviewStage
from app.services.runs import enqueue
from app.settings import settings
from redis import Redis
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import select


def recover():
    connection = Redis.from_url(settings.redis_url)
    counts = {"queued": 0, "abandoned": 0}
    with SessionLocal() as db:
        for run in db.scalars(select(ReviewRun).where(ReviewRun.status.in_(["QUEUED", "RUNNING"]))):
            try:
                job = Job.fetch(f"review-{run.id}", connection=connection)
                status = job.get_status(refresh=True).value
            except NoSuchJobError:
                status = "missing"
            if run.status == "QUEUED":
                if status not in ("queued", "started", "deferred", "scheduled"):
                    attempts = (run.error or {}).get("dispatch_attempts", 0) + 1
                    run.error = {
                        "code": "QUEUE_DISPATCH_RETRY",
                        "message": "任务尚未开始，正在恢复队列派发",
                        "dispatch_attempts": attempts,
                        "unknown_remote_state": False,
                    }
                    if attempts > 3:
                        run.status = "FAILED"
                        run.error = {
                            **run.error,
                            "code": "QUEUE_DISPATCH_FAILED",
                            "message": "队列派发连续失败，请检查 worker 后手动重试",
                        }
                        continue
                    db.commit()
                    enqueue(run.id)
                    counts["queued"] += 1
            elif status in ("failed", "stopped", "canceled", "finished", "missing"):
                run.status = "INCOMPLETE"
                run.assessment = "NOT_READY"
                run.error = {
                    "code": "WORKER_ABANDONED",
                    "message": "任务中断，已完成阶段保留；请核对后重试",
                    "unknown_remote_state": True,
                }
                run.revision += 1
                for stage in db.scalars(
                    select(ReviewStage).where(ReviewStage.run_id == run.id, ReviewStage.status == "RUNNING")
                ):
                    stage.status = "FAILED"
                    stage.error = run.error
                counts["abandoned"] += 1
        db.commit()
    return counts


if __name__ == "__main__":
    print(recover())
