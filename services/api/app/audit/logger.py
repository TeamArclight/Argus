import re
from typing import Any
from sqlalchemy.orm import Session
from app.models.domain import AuditEvent


def mask_sensitive_string(value: str) -> str:
    """Masks sensitive government identifiers (GSTIN, PAN, Aadhaar, CIN)."""
    if not value or len(value) < 4:
        return "****"

    # GSTIN (15 chars, e.g. 27AAAAA0000A1Z5)
    if re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$", value):
        return f"{value[:2]}A****{value[-3:]}"

    # PAN (10 chars, e.g. ABCDE1234F)
    if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$", value):
        return f"{value[:2]}****{value[-2:]}"

    # Aadhaar (12 digits, e.g. 123456789012)
    if re.match(r"^[0-9]{12}$", value):
        return f"****{value[-4:]}"

    # General fallback masking
    half = len(value) // 2
    return f"{value[:2]}****{value[-2:]}"


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively masks sensitive fields in audit payloads."""
    sanitized = {}
    sensitive_keys = {"gstin", "pan", "aadhaar", "account_number", "bank_account", "ssn", "secret"}

    for key, val in payload.items():
        if isinstance(val, dict):
            sanitized[key] = sanitize_payload(val)
        elif isinstance(val, str) and (key.lower() in sensitive_keys or any(k in key.lower() for k in ["gstin", "pan", "aadhaar"])):
            sanitized[key] = mask_sensitive_string(val)
        else:
            sanitized[key] = val
    return sanitized


class AuditLogger:
    """Audit logger service for ARGUS platform."""

    @staticmethod
    def log(
        db: Session,
        action: str,
        entity_type: str,
        entity_id: str,
        actor_id: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        payload = payload or {}
        sanitized = sanitize_payload(payload)

        audit_entry = AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            actor_role=actor_role,
            payload_json=sanitized,
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        return audit_entry
