"""Database-backed ARGUS processing worker with Phase 11 lock & event concurrency."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.domain import ProcessingJob, Tender, Bidder
from app.schemas.canonical import JobStage, JobStatus
from app.services.bid_verification_service import BidVerificationService
from app.services.job_event_service import JobEventService
from app.services.operation_lock_service import OperationLockService
from app.services.ai_adapter import AIServiceAdapter
from app.storage.factory import get_storage_provider

logger = logging.getLogger("argus.worker")


def claim_job():
    """Claims the next QUEUED job using transactional locking."""
    db = SessionLocal()
    try:
        bind = db.get_bind()
        query = db.query(ProcessingJob).filter(ProcessingJob.status == JobStatus.QUEUED).order_by(ProcessingJob.started_at.asc())
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
            
        job = query.first()
        if not job:
            return None

        resource_type = job.target_type
        resource_id = job.target_id
        operation = "PROCESS_TENDER" if job.job_type == "EXTRACT_REQUIREMENTS" else "VERIFY_BIDDER"

        try:
            OperationLockService.acquire_lock(
                db=db,
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
                job_id=job.id,
                principal_id="WORKER_DAEMON",
            )
        except Exception as lock_exc:
            logger.warning("Could not acquire lock for job %s: %s", job.id, lock_exc)
            return None

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        
        stage = JobStage.EXTRACTION if job.job_type == "EXTRACT_REQUIREMENTS" else JobStage.VERIFICATION
        JobEventService.emit_event(
            db=db,
            job_id=job.id,
            stage=stage,
            status=JobStatus.RUNNING,
            progress=10,
            message=f"Worker claimed and started processing {job.job_type}.",
        )
        db.commit()
        return job.id, job.job_type, job.target_id
    except Exception as exc:
        db.rollback()
        logger.exception("Error while claiming job: %s", exc)
        return None
    finally:
        db.close()


def _sanitize_worker_error(exc: Exception) -> str:
    """Sanitizes internal exception details before recording in job status or audit logs."""
    msg = str(exc).strip()
    if not msg:
        return "Internal processing failure."
    import re
    if re.search(r"(?:api_key|access_token|secret_key|private_key|password|jwt_secret)\b", msg, flags=re.IGNORECASE):
        return "Processing failed due to an authentication or configuration error."
    msg = re.sub(r"[A-Za-z]:\\[^ \t\n\r\"']+", "[path]", msg)
    msg = re.sub(r"/(?:app|home|var|tmp|Users)/[^ \t\n\r\"']+", "[path]", msg)
    return msg[:300] if len(msg) > 300 else msg


async def execute_job(job_id: str, job_type: str, target_id: str) -> None:
    """Executes a claimed background job with safe transaction boundaries and error categorization."""
    resource_type = "TENDER" if job_type == "EXTRACT_REQUIREMENTS" else "BIDDER"
    operation = "PROCESS_TENDER" if job_type == "EXTRACT_REQUIREMENTS" else "VERIFY_BIDDER"
    release_lock_on_exit = True

    try:
        if job_type == "EXTRACT_REQUIREMENTS":
            # 1. Read document metadata in short-lived session (Pre-flight deterministic check)
            db = SessionLocal()
            try:
                tender = db.query(Tender).filter(Tender.id == target_id).first()
                if not tender:
                    raise ValueError(f"Tender {target_id} not found.")
                from app.models.domain import Document, TenderRequirement
                doc = db.query(Document).filter(Document.tender_id == target_id).order_by(Document.created_at.desc()).first()
                if not doc:
                    raise ValueError(f"No document found for tender {target_id}.")
                doc_id = doc.id
                doc_sha = doc.sha256
                doc_uri = doc.storage_uri
                doc_filename = doc.filename
                doc_ctype = doc.content_type
            finally:
                db.close()

            # 2. Perform file read and async AI call outside of database transaction
            storage = get_storage_provider()
            file_bytes = storage.read_file(doc_uri)

            ai_adapter = AIServiceAdapter()
            try:
                result = await ai_adapter.extract_tender(
                    tender_id=target_id,
                    document_id=doc_id,
                    document_sha256=doc_sha,
                    file_bytes=file_bytes,
                    filename=doc_filename,
                    content_type=doc_ctype,
                )
            except Exception as net_err:
                # Ambiguous external network failure during extraction: fail closed
                release_lock_on_exit = False
                raise RuntimeError("External extraction operation timed out or interrupted; reconciliation required.") from net_err

            if not result.success:
                if result.error_code in ("DOCUMENT_NOT_FOUND", "INVALID_BASE64", "SHA256_MISMATCH"):
                    # Deterministic terminal failure
                    raise ValueError(f"Deterministic extraction failure: {result.message}")
                else:
                    # Ambiguous failure
                    release_lock_on_exit = False
                    raise RuntimeError(f"Extraction failed ambiguously: {result.message}")

            # 3. Persist results in a clean database transaction
            db = SessionLocal()
            try:
                existing_reqs = (
                    db.query(TenderRequirement)
                    .filter(TenderRequirement.tender_id == target_id)
                    .all()
                )

                def _norm_val(v: Any) -> str:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return str(float(v))
                    if isinstance(v, str):
                        return v.strip().lower()
                    return str(v)

                def _clause_key(c: str | None) -> str:
                    return (c or "").strip().lower()

                def _semantic_sig(req_type: Any, field_name: str | None, op: Any, exp_val: Any) -> tuple:
                    t = req_type.value if hasattr(req_type, "value") else str(req_type)
                    o = op.value if hasattr(op, "value") else str(op)
                    f = (field_name or "").strip().lower()
                    return (t.upper(), f, o.upper(), _norm_val(exp_val))

                approved_clauses = {}
                approved_sigs = {}
                unapproved_clauses = {}
                unapproved_sigs = {}

                for r in existing_reqs:
                    c_k = _clause_key(r.clause)
                    sig = _semantic_sig(r.requirement_type, r.field, r.operator, r.expected_value)
                    t_str = r.requirement_type.value if hasattr(r.requirement_type, "value") else str(r.requirement_type)
                    tc_sig = (c_k, t_str.upper()) if c_k else None

                    if r.is_approved:
                        if c_k:
                            approved_clauses[c_k] = r
                        approved_sigs[sig] = r
                        if tc_sig:
                            approved_sigs[tc_sig] = r
                    else:
                        if c_k and c_k not in unapproved_clauses:
                            unapproved_clauses[c_k] = r
                        if sig not in unapproved_sigs:
                            unapproved_sigs[sig] = r
                        if tc_sig and tc_sig not in unapproved_sigs:
                            unapproved_sigs[tc_sig] = r

                for req_dict in (result.data or []):
                    c_k = _clause_key(req_dict.get("clause"))
                    sig = _semantic_sig(req_dict["requirement_type"], req_dict["field"], req_dict["operator"], req_dict["expected_value"])
                    t_str = req_dict["requirement_type"].value if hasattr(req_dict["requirement_type"], "value") else str(req_dict["requirement_type"])
                    tc_sig = (c_k, t_str.upper()) if c_k else None

                    # Protect approved requirements
                    if (c_k and c_k in approved_clauses) or (sig in approved_sigs) or (tc_sig and tc_sig in approved_sigs):
                        continue

                    existing_unapproved = None
                    if tc_sig and tc_sig in unapproved_sigs:
                        existing_unapproved = unapproved_sigs[tc_sig]
                    elif sig in unapproved_sigs:
                        existing_unapproved = unapproved_sigs[sig]
                    elif c_k and c_k in unapproved_clauses:
                        existing_unapproved = unapproved_clauses[c_k]

                    if existing_unapproved:
                        existing_unapproved.confidence = max(existing_unapproved.confidence or 0.0, req_dict.get("confidence", 1.0))
                        if req_dict.get("source_text") and not existing_unapproved.source_text:
                            existing_unapproved.source_text = req_dict.get("source_text")
                        continue

                    req = TenderRequirement(
                        tender_id=target_id,
                        document_id=doc_id,
                        clause=req_dict["clause"],
                        requirement_type=req_dict["requirement_type"],
                        field=req_dict["field"],
                        operator=req_dict["operator"],
                        expected_value=req_dict["expected_value"],
                        unit=req_dict.get("unit"),
                        mandatory=req_dict.get("mandatory", True),
                        source_page=req_dict.get("source_page"),
                        source_text=req_dict.get("source_text"),
                        confidence=req_dict.get("confidence", 1.0),
                        requires_verification=req_dict.get("requires_verification", False),
                        is_approved=False,
                        metadata_json=req_dict.get("metadata_json", {}),
                    )
                    db.add(req)
                    if c_k:
                        unapproved_clauses[c_k] = req
                    unapproved_sigs[sig] = req
                    if tc_sig:
                        unapproved_sigs[tc_sig] = req

                job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                if job:
                    job.status = JobStatus.COMPLETED
                    job.progress = 100
                    job.completed_at = datetime.now(timezone.utc)
                    JobEventService.emit_event(
                        db=db,
                        job_id=job.id,
                        stage=JobStage.REPORTING,
                        status=JobStatus.COMPLETED,
                        progress=100,
                        message="Tender requirements extracted successfully.",
                    )
                db.commit()
            finally:
                db.close()

        elif job_type == "VERIFY_BIDDER":
            db = SessionLocal()
            try:
                svc = BidVerificationService(db)
                await svc.run_verification_workflow(
                    bidder_id=target_id,
                    job_id=job_id,
                    triggered_by="WORKER_DAEMON",
                    actor_id="WORKER_DAEMON",
                    actor_role="SYSTEM",
                )
            finally:
                db.close()
        else:
            db = SessionLocal()
            try:
                job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
                if job:
                    job.status = JobStatus.FAILED
                    job.error_message = f"Unsupported job type: {job_type}"
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
            finally:
                db.close()

    except Exception as exc:
        sanitized = _sanitize_worker_error(exc)
        logger.exception("Job %s execution failed: %s", job_id, sanitized)
        db = SessionLocal()
        try:
            job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
            if job:
                # If ambiguous, mark as REVIEW_REQUIRED or FAILED with recovery message
                job.status = JobStatus.REVIEW_REQUIRED if not release_lock_on_exit else JobStatus.FAILED
                job.error_message = sanitized
                job.completed_at = datetime.now(timezone.utc)
                JobEventService.emit_event(
                    db=db,
                    job_id=job.id,
                    stage=job.current_stage or JobStage.EXTRACTION,
                    status=job.status,
                    progress=100,
                    message=f"Job stopped: {sanitized}",
                )
                db.commit()
        except Exception as log_exc:
            logger.error("Failed to record job failure in DB: %s", log_exc)
            db.rollback()
        finally:
            db.close()
    finally:
        # Release lock only when state is definitively terminal and non-ambiguous
        if release_lock_on_exit:
            db = SessionLocal()
            try:
                OperationLockService.release_lock(
                    db=db,
                    resource_type=resource_type,
                    resource_id=target_id,
                    operation=operation,
                    job_id=job_id,
                )
            except Exception:
                pass
            finally:
                db.close()


def run(poll_interval: float = 1.0, max_iterations: int | None = None) -> None:
    """Worker polling loop."""
    logging.basicConfig(level=logging.INFO)
    iterations = 0
    while True:
        if max_iterations is not None and iterations >= max_iterations:
            break
        claimed = claim_job()
        if claimed:
            asyncio.run(execute_job(*claimed))
        else:
            time.sleep(poll_interval)
        iterations += 1


if __name__ == "__main__":
    run(poll_interval=float(settings.ARGUS_JOB_POLL_SECONDS if hasattr(settings, "ARGUS_JOB_POLL_SECONDS") else 2.0))
