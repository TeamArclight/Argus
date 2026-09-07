from typing import Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.domain import JobEvent, ProcessingJob
from app.schemas.canonical import JobStage, JobStatus


class JobEventService:
    """Service for allocating monotonic event sequences and recording JobEvents."""

    @classmethod
    def emit_event(
        cls,
        db: Session,
        job_id: str,
        stage: JobStage | str,
        status: JobStatus | str,
        progress: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> JobEvent:
        """Atomically allocates the next monotonic sequence number for job_id and records JobEvent."""
        if db.bind is not None and db.bind.dialect.name == 'postgresql':
            db.execute(
                select(ProcessingJob.id).where(ProcessingJob.id == job_id).with_for_update()
            )

        stmt = select(func.coalesce(func.max(JobEvent.seq), 0)).where(JobEvent.job_id == job_id)
        max_seq = db.execute(stmt).scalar() or 0
        next_seq = max_seq + 1

        stage_val = stage.value if hasattr(stage, 'value') else str(stage)
        status_val = status.value if hasattr(status, 'value') else str(status)

        event = JobEvent(
            seq=next_seq,
            job_id=job_id,
            stage=stage_val,
            status=status_val,
            progress=progress,
            message=message,
            payload=payload or {},
        )
        db.add(event)
        db.flush()
        return event
