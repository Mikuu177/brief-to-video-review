from uuid import uuid4

from app.services.runs import enqueue, error_detail
from app.settings import settings
from redis import Redis
from rq import Queue
from rq.timeouts import JobTimeoutException


def test_long_job_budget_is_persisted_without_duplicate_dispatch(monkeypatch):
    queue_name = f"test-budget-{uuid4().hex}"
    monkeypatch.setattr(settings, "queue_name", queue_name)
    monkeypatch.setattr(settings, "review_job_timeout_seconds", 7200)
    queue = Queue(queue_name, connection=Redis.from_url(settings.redis_url))
    run_id = str(uuid4())
    try:
        enqueue(run_id)
        enqueue(run_id)
        job = queue.fetch_job(f"review-{run_id}")
        assert job is not None and job.timeout == 7200
        assert queue.job_ids == [job.id]
    finally:
        queue.empty()
        queue.delete()


def test_worker_deadline_keeps_remote_state_unknown():
    detail = error_detail(JobTimeoutException("Execution exceeded job timeout"))
    assert detail["code"] == "WORKER_TIME_BUDGET_EXCEEDED"
    assert detail["unknown_remote_state"] is True
