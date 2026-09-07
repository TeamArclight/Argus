import hashlib
import json
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.domain import IdempotencyRecord


class IdempotencyService:
    """Database-backed request idempotency and deduplication service."""

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
        """Checks existing idempotency record or initializes new record.

        Returns (cached_response_json, cached_status_code, record).
        """
        if not key or not key.strip():
            return None, None, None

        key = key.strip()[:255]
        req_hash = cls.hash_payload(payload)

        existing = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.key == key,
                IdempotencyRecord.principal_id == principal_id,
            )
            .first()
        )

        if existing:
            if existing.operation != operation or existing.request_hash != req_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_COLLISION",
                        "message": "Idempotency key collision: provided key was previously used with different request parameters or operations.",
                        "details": {"key": key, "existing_operation": existing.operation, "requested_operation": operation},
                    },
                )

            if existing.status == "PROCESSING":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "OPERATION_IN_PROGRESS",
                        "message": "An identical operation with this idempotency key is currently processing.",
                        "details": {"key": key, "operation": operation},
                    },
                )
            if existing.status == "COMPLETED":
                return existing.response_json, existing.response_code or 200, existing

        # Create new processing record
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

    @classmethod
    def complete(cls, db: Session, record: IdempotencyRecord | None, response_code: int, response_json: dict[str, Any]) -> None:
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
