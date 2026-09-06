import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import ProcessingJob
from app.schemas.canonical import JobRead

router = APIRouter(prefix="/jobs", tags=["Jobs & SSE"])


@router.get("/{id}", response_model=JobRead)
def get_job_status(id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {id} not found.",
        )
    return job


@router.get("/{id}/events", response_class=StreamingResponse)
async def stream_job_events(id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {id} not found.",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        # Initial event stream frame
        init_payload = {
            "job_id": job.id,
            "stage": job.current_stage.value if hasattr(job.current_stage, "value") else str(job.current_stage),
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "progress": job.progress,
            "message": f"Job {job.id} initialized.",
        }
        yield f"data: {json.dumps(init_payload)}\n\n"

        # Emit simulated progress updates until job completion
        progress_steps = [
            ("PARSING", 25, "Document OCR and table extraction"),
            ("VERIFICATION", 50, "Executing registry verification adapters"),
            ("COMPLIANCE", 80, "Evaluating deterministic compliance rules"),
            ("REPORTING", 100, "Verification job finished"),
        ]

        for stage, prog, msg in progress_steps:
            await asyncio.sleep(0.1)
            data = {
                "job_id": job.id,
                "stage": stage,
                "status": "RUNNING" if prog < 100 else "COMPLETED",
                "progress": prog,
                "message": msg,
            }
            yield f"data: {json.dumps(data)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
