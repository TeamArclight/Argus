from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.domain import IdempotencyRecord, ProcessingJob
from app.schemas.canonical import JobStatus

IDEMPOTENCY_KEY_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,255}$")
PROCESSING_TIMEOUT_MINUTES = 10


def validate_idempotency_key(key: str | None) -> str | None:
    """Validates idempotency key format without silent truncation."""
    if key is None:
        return None
    if not isinstance(key, str) or not key.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_IDEMPOTENCY_KEY",
                "message": "Idempotency key must be a non-empty string of 1-255 characters containing alphanumeric, hyphen, or underscore characters.",
            },
        )
    key_clean = key.strip()
    if not IDEMPOTENCY_KEY_REGEX.match(key_clean):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_IDEMPOTENCY_KEY",
                "message": "Idempotency key must be 1-255 characters containing only alphanumeric, hyphen, or underscore characters.",
            },
        )
    return key_clean


class IdempotencyService:
    """Database-backed request idempotency, deduplication, and crash recovery service."""

    @staticmethod
    def hash_payload(payload: Any) -> str:
        """Computes deterministic SHA-256 hash of payload data."""
        if payload is None:
            raw = ""
        elif isinstance(payload, bytes):
            raw = payload.hex()
        elif isinstance(payload, str):
            raw = payload
        else:
            try:
                raw = json.dumps(payload, sort_keys=True)
            except Exception:
                raw = str(payload)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
        """Checks existing idempotency record or initializes new record within transactional boundary.

        Returns (cached_response_json, cached_status_code, record).
        """
        valid_key = validate_idempotency_key(key)
        if not valid_key:
            return None, None, None

        req_hash = cls.hash_payload(payload)

        # Check for existing record matching principal, resource scope, and key
        existing = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.principal_id == principal_id,
                IdempotencyRecord.resource_type == resource_type,
                IdempotencyRecord.resource_id == resource_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == valid_key,
            )
            .first()
        )

        if not existing:
            # Check for cross-resource key collision (same key used for different scope by same principal)
            cross_scope_collision = (
                db.query(IdempotencyRecord)
                .filter(
                    IdempotencyRecord.principal_id == principal_id,
                    IdempotencyRecord.key == valid_key,
                )
                .first()
            )
            if cross_scope_collision:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_COLLISION",
                        "message": "Idempotency key collision: key was previously used for a different operation or resource.",
                        "details": {"key": valid_key, "existing_resource": cross_scope_collision.resource_id},
                    },
                )

        if existing:
            if existing.request_hash != req_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_COLLISION",
                        "message": "Idempotency key collision: provided key was previously used with different request parameters.",
                        "details": {"key": valid_key, "operation": operation},
                    },
                )

            if existing.status == "COMPLETED":
                return existing.response_json, existing.response_code or 200, existing

            if existing.status == "PROCESSING":
                # Check for crashed/stale processing or job failure recovery
                is_stale = (datetime.now(timezone.utc) - existing.created_at.replace(tzinfo=timezone.utc)).total_seconds() > (PROCESSING_TIMEOUT_MINUTES * 60)
                job_failed = False
                if existing.job_id:
                    job = db.query(ProcessingJob).filter(ProcessingJob.id == existing.job_id).first()
                    if job and job.status == JobStatus.FAILED:
                        job_failed = True

                if not is_stale and not job_failed:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "OPERATION_IN_PROGRESS",
                            "message": "An identical operation with this idempotency key is currently processing.",
                            "details": {"key": valid_key, "operation": operation},
                        },
                    )

                # Recover crashed/failed processing record for safe retry
                existing.status = "PROCESSING"
                existing.created_at = datetime.now(timezone.utc)
                db.commit()
                return None, None, existing

            if existing.status == "FAILED":
                # Allow safe retry for previously failed operations
                existing.status = "PROCESSING"
                existing.created_at = datetime.now(timezone.utc)
                db.commit()
                return None, None, existing

        # Create new processing record with DB IntegrityError handling for concurrent inserts
        try:
            record = IdempotencyRecord(
                key=valid_key,
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
            # Race condition recovery: re-fetch record that won the insert race
            winner = (
                db.query(IdempotencyRecord)
                .filter(
                    IdempotencyRecord.principal_id == principal_id,
                    IdempotencyRecord.resource_type == resource_type,
                    IdempotencyRecord.resource_id == resource_id,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.key == valid_key,
                )
                .first()
            )
            if winner and winner.status == "COMPLETED":
                return winner.response_json, winner.response_code or 200, winner
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OPERATION_IN_PROGRESS",
                    "message": "An identical operation with this idempotency key is currently processing.",
                    "details": {"key": valid_key, "operation": operation},
                },
            )

    @classmethod
    def link_job(cls, db: Session, record: IdempotencyRecord | None, job_id: str) -> None:
        """Links processing job ID to idempotency record."""
        if not record:
            return
        record.job_id = job_id
        db.commit()

    @classmethod
    def complete(cls, db: Session, record: IdempotencyRecord | None, response_code: int, response_json: dict[str, Any]) -> None:
        """Marks idempotency record as COMPLETED and stores response payload strictly after business transaction commits."""
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
