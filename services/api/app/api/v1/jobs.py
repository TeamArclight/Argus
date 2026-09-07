import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_principal
from app.db.session import SessionLocal, get_db
from app.models.domain import JobEvent, ProcessingJob
from app.schemas.canonical import AuthenticatedPrincipal, JobRead, JobStatus

router = APIRouter(prefix="/jobs", tags=["Jobs & SSE"])


@router.get("/{id}", response_model=JobRead)
def get_job_status(
    id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {id} not found.",
        )
    return job


@router.get("/{id}/events", response_class=StreamingResponse)
async def stream_job_events(
    id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """Streams real-time Server-Sent Events (SSE) for job progress with reconnection support."""
    # Verify job existence and ownership via initial short-lived DB session
    with SessionLocal() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Processing job with ID {id} not found.",
            )

    async def event_generator() -> AsyncGenerator[str, None]:
        last_seen_event_id = last_event_id
        ping_interval = 15.0
        elapsed_ping = 0.0

        while True:
            if await request.is_disconnected():
                break

            # Short-lived DB session query to prevent blocking DB connection pool during SSE loop
            with SessionLocal() as db:
                current_job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
                if not current_job:
                    break

                query = db.query(JobEvent).filter(JobEvent.job_id == id)
                if last_seen_event_id:
                    # Fetch events newer than Last-Event-ID
                    query = query.filter(JobEvent.id > last_seen_event_id)
                
                new_events = query.order_by(JobEvent.timestamp.asc(), JobEvent.id.asc()).all()

                for ev in new_events:
                    payload = {
                        "job_id": current_job.id,
                        "stage": ev.stage.value if hasattr(ev.stage, "value") else str(ev.stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": ev.progress,
                        "message": ev.message,
                        "details": ev.details_json,
                    }
                    last_seen_event_id = ev.id
                    yield f"id: {ev.id}\nevent: job_event\ndata: {json.dumps(payload)}\n\n"
                    elapsed_ping = 0.0

                # Check terminal state
                if current_job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    # Send final status frame if no new events
                    final_payload = {
                        "job_id": current_job.id,
                        "stage": current_job.current_stage.value if hasattr(current_job.current_stage, "value") else str(current_job.current_stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": current_job.progress,
                        "message": current_job.error_message or f"Job {current_job.id} reached terminal state {current_job.status.value}.",
                    }
                    yield f"event: job_terminal\ndata: {json.dumps(final_payload)}\n\n"
                    break

            await asyncio.sleep(0.5)
            elapsed_ping += 0.5

            if elapsed_ping >= ping_interval:
                # SSE heartbeat ping comment (does not create DB records or advance event IDs)
                yield ": ping\n\n"
                elapsed_ping = 0.0

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
