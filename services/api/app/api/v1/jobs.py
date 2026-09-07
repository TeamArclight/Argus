import asyncio
import json
import re
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_principal
from app.db.session import SessionLocal, get_db
from app.models.domain import JobEvent, ProcessingJob
from app.schemas.canonical import AuthenticatedPrincipal, JobRead, JobStatus

router = APIRouter(prefix="/jobs", tags=["Jobs & SSE"])

SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(password|secret|token|api[_-]?key|bearer\s+[a-zA-Z0-9_\-\.]+)\s*[:=]\s*["\']?([^"\'\s]+)["\']?'),
    re.compile(r'(?i)(postgres|postgresql|mysql|sqlite)://[^\s]+'),
    re.compile(r'(?i)Traceback \(most recent call last\):[\s\S]*'),
]


def sanitize_text(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text
    for pattern in SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def sanitize_dict(d: Any) -> Any:
    if isinstance(d, dict):
        sanitized = {}
        for k, v in d.items():
            if any(s in str(k).lower() for s in ("password", "secret", "token", "key", "credential", "auth")):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_dict(v)
        return sanitized
    elif isinstance(d, list):
        return [sanitize_dict(item) for item in d]
    elif isinstance(d, str):
        return sanitize_text(d)
    return d


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
    # Verify job existence via initial short-lived DB session
    with SessionLocal() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Processing job with ID {id} not found.",
            )

    async def event_generator() -> AsyncGenerator[str, None]:
        last_seen_seq = 0
        if last_event_id:
            try:
                last_seen_seq = int(last_event_id)
            except (ValueError, TypeError):
                last_seen_seq = 0

        ping_interval = 15.0
        elapsed_ping = 0.0

        while True:
            if await request.is_disconnected():
                break

            events_to_send = []
            is_terminal = False
            terminal_payload = None

            # Short-lived DB session query to prevent blocking DB connection pool during SSE loop
            with SessionLocal() as db:
                current_job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
                if not current_job:
                    break

                query = db.query(JobEvent).filter(JobEvent.job_id == id)
                if last_seen_seq > 0:
                    query = query.filter(JobEvent.seq > last_seen_seq)

                new_events = query.order_by(JobEvent.seq.asc(), JobEvent.timestamp.asc()).all()

                for ev in new_events:
                    events_to_send.append({
                        "id": ev.seq,
                        "job_id": current_job.id,
                        "stage": ev.stage.value if hasattr(ev.stage, "value") else str(ev.stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": ev.progress,
                        "message": sanitize_text(ev.message),
                        "details": sanitize_dict(ev.payload) if ev.payload else {},
                    })
                    last_seen_seq = ev.seq

                # Check terminal state
                if current_job.status in (JobStatus.COMPLETED, JobStatus.FAILED, "COMPLETED", "FAILED"):
                    is_terminal = True
                    stage_str = current_job.current_stage.value if hasattr(current_job.current_stage, "value") else str(current_job.current_stage)
                    status_str = current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status)
                    terminal_payload = {
                        "job_id": current_job.id,
                        "stage": stage_str,
                        "status": status_str,
                        "progress": current_job.progress,
                        "message": sanitize_text(current_job.error_message) or f"Job {current_job.id} reached terminal state {status_str}.",
                    }

            # Yield events outside of the database session
            for ev_data in events_to_send:
                yield f"id: {ev_data['id']}\nevent: job_event\ndata: {json.dumps(ev_data)}\n\n"
                elapsed_ping = 0.0

            if is_terminal:
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
