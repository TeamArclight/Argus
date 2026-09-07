import hashlib
import json
import re
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.domain import IdempotencyRecord, ProcessingJob
from app.schemas.canonical import JobStatus

IDEMPOTENCY_KEY_REGEX = re.compile(r"^[a-zA-Z0-9_\-.:]{1,128}$")


class IdempotencyService:
    """Database-backed request idempotency and deduplication service."""

    @staticmethod
    def validate_key(key: str | None) -> None:
        """Validates idempotency key format and length."""
        if key is None:
            return
        if not (1 <= len(key) <= 128) or not IDEMPOTENCY_KEY_REGEX.match(key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_IDEMPOTENCY_KEY",
                    "message": "Idempotency key must be 1-128 characters containing only letters, numbers, underscores, hyphens, periods, or colons.",
                    "details": {"key": key[:32]},
                },
            )

    @classmethod
    def _compute_hash(
        cls,
        principal_id: str,
        resource_type: str,
        resource_id: str,
        operation: str,
        payload: Any,
    ) -> str:
        """Computes deterministic SHA-256 fingerprint over scope and payload."""
        if payload is None:
            raw_payload = ""
        elif isinstance(payload, bytes):
            raw_payload = payload.hex()
        elif isinstance(payload, str):
            raw_payload = payload
        else:
            try:
                raw_payload = json.dumps(payload, sort_keys=True)
            except Exception:
                raw_payload = str(payload)
        
        fingerprint = f"{principal_id}|{resource_type}|{resource_id}|{operation}|{raw_payload}"
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    @classmethod
    def get_record(
        cls,
        db: Session,
        principal_id: str,
        resource_type: str,
        resource_id: str,
        operation: str,
        key: str,
    ) -> IdempotencyRecord | None:
        return (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.key == key,
                IdempotencyRecord.principal_id == principal_id,
                IdempotencyRecord.resource_type == resource_type,
                IdempotencyRecord.resource_id == resource_id,
                IdempotencyRecord.operation == operation,
            )
            .first()
        )

    @classmethod
    def check_or_start(
        cls,
        db: Session,
        key: str | None,
        principal_id: str,
        resource_type: str,
        resource_id: str,
        operation: str,
        payload: Any = None,
    ) -> tuple[dict[str, Any] | None, int | None, IdempotencyRecord | None]:
        """Checks existing idempotency record or initializes new record with concurrency lock.

        Returns (cached_response_json, cached_status_code, record).
        """
        if not key or not key.strip():
            return None, None, None

        cls.validate_key(key)
        req_hash = cls._compute_hash(principal_id, resource_type, resource_id, operation, payload)

        # Check for any records using this key by this principal
        existing_records = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.key == key,
                IdempotencyRecord.principal_id == principal_id,
            )
            .all()
        )

        for existing in existing_records:
            # Check scope and fingerprint collision
            if (
                existing.resource_type != resource_type
                or existing.resource_id != resource_id
                or existing.operation != operation
                or existing.request_hash != req_hash
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_COLLISION",
                        "message": "Idempotency key collision: provided key was previously used with different scope, resource, or payload.",
                        "details": {
                            "key": key,
                            "existing_scope": f"{existing.resource_type}:{existing.resource_id}:{existing.operation}",
                            "requested_scope": f"{resource_type}:{resource_id}:{operation}",
                        },
                    },
                )

            # Exact scope match
            if existing.status == "COMPLETED":
                return existing.response_json, existing.response_code or 200, existing

            if existing.status == "PROCESSING":
                if existing.job_id:
                    job = db.query(ProcessingJob).filter(ProcessingJob.id == existing.job_id).first()
                    if job and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "code": "OPERATION_IN_PROGRESS",
                                "message": "An identical operation with this idempotency key is currently processing.",
                                "details": {"key": key, "job_id": job.id},
                            },
                        )
                    elif job and job.status == JobStatus.COMPLETED and existing.response_json:
                        return existing.response_json, existing.response_code or 200, existing
                    elif job and job.status == JobStatus.FAILED:
                        # Crashed / failed job recovery: allow retry to reset and proceed
                        existing.status = "PROCESSING"
                        existing.response_code = None
                        existing.response_json = None
                        existing.job_id = None
                        db.commit()
                        db.refresh(existing)
                        return None, None, existing

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "OPERATION_IN_PROGRESS",
                        "message": "An identical operation with this idempotency key is currently processing.",
                        "details": {"key": key, "operation": operation},
                    },
                )

        # Create new processing record wrapped in concurrency-safe transaction
        try:
            record = IdempotencyRecord(
                key=key,
                principal_id=principal_id,
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
                request_hash=req_hash,
                status="PROCESSING",
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return None, None, record
        except IntegrityError:
            db.rollback()
            existing = cls.get_record(db, principal_id, resource_type, resource_id, operation, key)
            if existing:
                if existing.request_hash != req_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "IDEMPOTENCY_KEY_COLLISION",
                            "message": "Concurrent idempotency key collision detected.",
                            "details": {"key": key},
                        },
                    )
                if existing.status == "COMPLETED":
                    return existing.response_json, existing.response_code or 200, existing
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "OPERATION_IN_PROGRESS",
                        "message": "Concurrent operation with this idempotency key is currently processing.",
                        "details": {"key": key},
                    },
                )
            raise

    @classmethod
    def attach_job(
        cls,
        db: Session,
        record: IdempotencyRecord | None,
        job_id: str,
        run_id: str | None = None,
    ) -> None:
        """Attaches background job and compliance run identifiers to the idempotency record."""
        if not record:
            return
        record.job_id = job_id
        if run_id:
            record.run_id = run_id
        db.commit()

    @classmethod
    def complete(
        cls,
        db: Session,
        record: IdempotencyRecord | None,
        response_code: int,
        response_json: dict[str, Any],
    ) -> None:
        """Marks idempotency record as COMPLETED and stores response payload."""
        if not record:
            return
        record.status = "COMPLETED"
        record.response_code = response_code
        record.response_json = response_json
        db.commit()

    @classmethod
    def fail(cls, db: Session, record: IdempotencyRecord | None) -> None:
        """Marks idempotency record as FAILED."""
        if not record:
            return
        record.status = "FAILED"
        db.commit()

