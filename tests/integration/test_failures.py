from uuid import uuid4

from app.errors import ReviewError
from app.models import ReviewRun, ReviewStage
from app.services.runs import stage


def test_completed_stages_are_reused_and_failed_stages_get_new_attempt(environment, project):
    _, sessions = environment
    with sessions() as db:
        run = ReviewRun(
            project_id=project["id"],
            kind="script",
            input_snapshot={},
            input_hash="test",
            idempotency_key=str(uuid4()),
        )
        db.add(run)
        db.commit()
        count = {"calls": 0}

        def succeed():
            count["calls"] += 1
            return {"actual": "stage result"}

        assert stage(db, run, "observed", succeed) == stage(db, run, "observed", succeed)
        assert count["calls"] == 1

        def fail():
            raise ReviewError("PROVIDER_TIMEOUT", "模拟供应商超时", unknown_remote=True)

        import pytest

        with pytest.raises(ReviewError):
            stage(db, run, "later", fail)
        stage(db, run, "later", succeed)
        from sqlalchemy import select

        attempts = list(
            db.scalars(
                select(ReviewStage)
                .where(ReviewStage.run_id == run.id, ReviewStage.name == "later")
                .order_by(ReviewStage.attempt)
            )
        )
        assert [s.status for s in attempts] == ["FAILED", "COMPLETED"]
        assert attempts[0].error["unknown_remote_state"]


def test_cancel_before_next_stage_does_not_execute_it(environment, project):
    _, sessions = environment
    import pytest

    with sessions() as db:
        run = ReviewRun(
            project_id=project["id"],
            kind="script",
            input_snapshot={},
            input_hash="test",
            idempotency_key=str(uuid4()),
            cancel_requested=True,
        )
        db.add(run)
        db.commit()
        with pytest.raises(ReviewError, match="取消"):
            stage(db, run, "must_not_run", lambda: (_ for _ in ()).throw(AssertionError()))
