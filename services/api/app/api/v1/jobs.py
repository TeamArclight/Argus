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


def sanitize_message(msg: str | None) -> str:
    if not msg:
        return ""
    if "Traceback (most recent call last):" in msg or 'File "' in msg:
        return "An error occurred during background job processing."
    return msg


def sanitize_details(details: Any) -> Any:
    if isinstance(details, dict):
        cleaned = {}
        for k, v in details.items():
            if isinstance(v, str) and ("Traceback (most recent call last):" in v or 'File "' in v):
                cleaned[k] = "An error occurred during background job processing."
            else:
                cleaned[k] = sanitize_details(v)
        return cleaned
    elif isinstance(details, list):
        return [sanitize_details(item) for item in details]
    return details


@router.get("/{id}/events", response_class=StreamingResponse)
async def stream_job_events(
    id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """Streams real-time Server-Sent Events (SSE) for job progress with monotonic integer sequence reconnection support."""
    # Verify job existence and RBAC authorization via initial short-lived DB session
    with SessionLocal() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Processing job with ID {id} not found.",
            )

    async def event_generator() -> AsyncGenerator[str, None]:
        # Parse Last-Event-ID header as integer sequence cursor if available
        last_seen_seq = 0
        if last_event_id is not None:
            try:
                last_seen_seq = int(last_event_id)
            except ValueError:
                last_seen_seq = 0

        ping_interval = 15.0
        elapsed_ping = 0.0

        while True:
            if await request.is_disconnected():
                break

            events_to_yield: list[tuple[int, dict[str, Any]]] = []
            is_terminal = False
            terminal_payload: dict[str, Any] | None = None

            # Short-lived DB session query to prevent blocking DB connection pool during SSE loop
            with SessionLocal() as db:
                current_job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
                if not current_job:
                    break

                query = db.query(JobEvent).filter(JobEvent.job_id == id)
                if last_seen_seq > 0:
                    query = query.filter(JobEvent.seq > last_seen_seq)
                
                # Fetch bounded batch of events ordered by monotonic seq cursor
                batch = query.order_by(JobEvent.seq.asc(), JobEvent.timestamp.asc()).limit(50).all()

                for ev in batch:
                    ev_seq = ev.seq if ev.seq is not None else 0
                    payload = {
                        "job_id": current_job.id,
                        "stage": ev.stage.value if hasattr(ev.stage, "value") else str(ev.stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": ev.progress,
                        "message": sanitize_message(ev.message),
                        "details": sanitize_details(ev.payload.get("details") if isinstance(ev.payload, dict) else ev.payload),
                    }
                    events_to_yield.append((ev_seq, payload))

                # Check terminal state when no more events are pending
                if current_job.status in (JobStatus.COMPLETED, JobStatus.FAILED) and not batch:
                    is_terminal = True
                    terminal_payload = {
                        "job_id": current_job.id,
                        "stage": current_job.current_stage.value if hasattr(current_job.current_stage, "value") else str(current_job.current_stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": current_job.progress,
                        "message": sanitize_message(current_job.error_message or f"Job {current_job.id} reached terminal state {current_job.status.value}."),
                    }

            # IMPORTANT: DB session is CLOSED HERE before yielding SSE frames to prevent connection pool starvation
            for seq_val, payload_dto in events_to_yield:
                last_seen_seq = seq_val
                yield f"id: {seq_val}\nevent: job_event\ndata: {json.dumps(payload_dto)}\n\n"
                elapsed_ping = 0.0

            if is_terminal and not events_to_yield and terminal_payload is not None:
                yield f"event: job_terminal\ndata: {json.dumps(terminal_payload)}\n\n"
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
