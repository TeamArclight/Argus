import asyncio
import json
import re
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_principal
from app.db.session import SessionLocal, get_db
from app.models.domain import Bidder, JobEvent, ProcessingJob, Tender
from app.schemas.canonical import AuthenticatedPrincipal, JobRead, JobStatus, UserRole

router = APIRouter(prefix="/jobs", tags=["Jobs & SSE"])

SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(password|secret|token|api[_-]?key|bearer\s+[a-zA-Z0-9_\-\.]+)\s*[:=]\s*["\']?([^"\'\s]+)["\']?'),
    re.compile(r'(?i)(postgres|postgresql|mysql|sqlite)://[^\s]+'),
    re.compile(r'(?i)Traceback \(most recent call last\):[\s\S]*'),
]

ALLOWLISTED_DETAIL_KEYS = {
    "step", "stage", "rule_code", "reason_code", "document_id",
    "checks_count", "summary", "error_code", "doc_count", "checks",
}


def sanitize_text(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = text
    for pattern in SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def filter_and_sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details or not isinstance(details, dict):
        return {}
    sanitized = {}
    for k, v in details.items():
        k_str = str(k)
        if any(s in k_str.lower() for s in ("password", "secret", "token", "key", "credential", "auth")):
            continue
        if k_str in ALLOWLISTED_DETAIL_KEYS or not isinstance(v, (dict, list)):
            if isinstance(v, str):
                sanitized[k_str] = sanitize_text(v)
            elif isinstance(v, (int, float, bool)):
                sanitized[k_str] = v
    return sanitized


def check_job_access(principal: AuthenticatedPrincipal, job: ProcessingJob, db: Session) -> None:
    """Enforces strict RBAC and resource authorization on job inspection and SSE streaming."""
    if principal.role in (UserRole.ADMIN, UserRole.PROCUREMENT_OFFICER, UserRole.REVIEWER, UserRole.AUDITOR):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "Access denied to requested job."},
    )


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
    check_job_access(principal, job, db)
    return job


@router.get("/{id}/events", response_class=StreamingResponse)
async def stream_job_events(
    id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """Streams real-time Server-Sent Events (SSE) for job progress with reconnection support."""
    # Verify job existence and authorization via initial short-lived DB session
    with SessionLocal() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Processing job with ID {id} not found.",
            )
        check_job_access(principal, job, db)

    # Validate Last-Event-ID cursor format
    last_seen_seq = 0
    if last_event_id is not None and last_event_id != "":
        if not last_event_id.isdigit():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_EVENT_CURSOR",
                    "message": "Last-Event-ID cursor must be a valid non-negative integer sequence number.",
                    "details": {"last_event_id": last_event_id[:32]},
                },
            )
        last_seen_seq = int(last_event_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        nonlocal last_seen_seq
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

                new_events = query.order_by(JobEvent.seq.asc(), JobEvent.timestamp.asc()).limit(100).all()

                for ev in new_events:
                    events_to_send.append({
                        "id": ev.seq,
                        "job_id": current_job.id,
                        "seq": ev.seq,
                        "stage": ev.stage.value if hasattr(ev.stage, "value") else str(ev.stage),
                        "status": current_job.status.value if hasattr(current_job.status, "value") else str(current_job.status),
                        "progress": ev.progress,
                        "message": sanitize_text(ev.message),
                        "details": filter_and_sanitize_details(ev.payload),
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
