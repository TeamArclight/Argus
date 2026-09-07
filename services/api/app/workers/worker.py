"""Database-backed ARGUS processing worker."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.api.v1.bidders import verify_bidder
from app.api.v1.tenders import process_tender
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.domain import ProcessingJob
from app.schemas.canonical import JobStatus

logger = logging.getLogger("argus.worker")


def claim_job():
    db = SessionLocal()
    try:
        job = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.status == JobStatus.QUEUED)
            .order_by(ProcessingJob.started_at.asc())
            .first()
        )
        if not job:
            return None
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()
        return job.id, job.job_type, job.target_id
    finally:
        db.close()


async def execute_job(job_id: str, job_type: str, target_id: str) -> None:
    db = SessionLocal()
    try:
        if job_type == "EXTRACT_REQUIREMENTS":
            await process_tender(target_id, db, internal_job_id=job_id)
        elif job_type == "VERIFY_BIDDER":
            await verify_bidder(target_id, db, internal_job_id=job_id)
        else:
            job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if job:
                job.status = JobStatus.FAILED
                job.error_message = f"Unsupported job type: {job_type}"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        if job:
            job.status = JobStatus.FAILED
            job.error_message = "Worker execution failed"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        claimed = claim_job()
        if claimed:
            asyncio.run(execute_job(*claimed))
        else:
            time.sleep(settings.ARGUS_JOB_POLL_SECONDS)


if __name__ == "__main__":
    run()
