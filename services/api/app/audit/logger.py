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
    sensitive_keys = {
        "gstin", "pan", "aadhaar", "account_number", "bank_account", "ssn",
        "secret", "password", "jwt", "token", "access_token", "api_key", "client_secret"
    }

    for key, val in payload.items():
        if isinstance(val, dict):
            sanitized[key] = sanitize_payload(val)
        elif isinstance(val, str) and (key.lower() in sensitive_keys or any(k in key.lower() for k in ["gstin", "pan", "aadhaar", "password", "token", "secret"])):
            sanitized[key] = mask_sensitive_string(val)
        else:
            sanitized[key] = val
    return sanitized


class AuditLogger:
    """Audit logger service for ARGUS platform."""

    @staticmethod
    def create_entry(
        db: Session,
        action: str,
        entity_type: str,
        entity_id: str,
        actor_id: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        payload: dict[str, Any] | None = None,
        actor_name: str | None = None,
        actor_email: str | None = None,
        principal: Any | None = None,
    ) -> AuditEvent:
        """Adds an AuditEvent to the session without calling commit(), enabling single-transaction persistence."""
        payload_data = dict(payload or {})

        # If authenticated principal is provided, strictly derive actor identity from verified claims
        if principal is not None:
            actor_id = getattr(principal, "user_id", actor_id)
            role_obj = getattr(principal, "role", actor_role)
            actor_role = role_obj.value if hasattr(role_obj, "value") else str(role_obj)
            actor_name = getattr(principal, "full_name", None) or getattr(principal, "name", None) or actor_name
            actor_email = getattr(principal, "email", None) or actor_email

        # Populate actor identity snapshot fields in payload_json
        if actor_id:
            payload_data["actor_user_id"] = actor_id
        if actor_name:
            payload_data["actor_name"] = actor_name
        if actor_email:
            payload_data["actor_email"] = actor_email
        if actor_role:
            payload_data["actor_role"] = actor_role

        sanitized = sanitize_payload(payload_data)

        audit_entry = AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            actor_role=actor_role,
            payload_json=sanitized,
        )
        db.add(audit_entry)
        return audit_entry

    @staticmethod
    def log(
        db: Session,
        action: str,
        entity_type: str,
        entity_id: str,
        actor_id: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        payload: dict[str, Any] | None = None,
        actor_name: str | None = None,
        actor_email: str | None = None,
        principal: Any | None = None,
    ) -> AuditEvent:
        audit_entry = AuditLogger.create_entry(
            db,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=payload,
            actor_name=actor_name,
            actor_email=actor_email,
            principal=principal,
        )
        db.commit()
        db.refresh(audit_entry)
        return audit_entry

    @staticmethod
    def log_live(
        db: Session,
        action: str,
        entity_type: str,
        entity_id: str,
        actor_id: str = "SYSTEM",
        actor_role: str = "SYSTEM",
        payload: dict[str, Any] | None = None,
        actor_name: str | None = None,
        actor_email: str | None = None,
        principal: Any | None = None,
    ) -> AuditEvent:
        """Immediately writes and commits an AuditEvent in an isolated session/transaction.
        This provides real-time telemetry to polling clients while preserving the caller's
        uncommitted business transaction on `db`.
        """
        bind = None
        try:
            bind = db.get_bind()
        except Exception:
            pass

        isolated_db = None
        try:
            if bind is not None:
                from sqlalchemy.orm import sessionmaker
                IsolatedSession = sessionmaker(autocommit=False, autoflush=False, bind=bind)
                isolated_db = IsolatedSession()
            else:
                from app.db.session import SessionLocal
                isolated_db = SessionLocal()

            entry = AuditLogger.create_entry(
                isolated_db,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor_id=actor_id,
                actor_role=actor_role,
                payload=payload,
                actor_name=actor_name,
                actor_email=actor_email,
                principal=principal,
            )
            isolated_db.commit()
            isolated_db.refresh(entry)
            return entry
        except Exception:
            if isolated_db:
                try:
                    isolated_db.rollback()
                except Exception:
                    pass
            return AuditLogger.create_entry(
                db,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor_id=actor_id,
                actor_role=actor_role,
                payload=payload,
                actor_name=actor_name,
                actor_email=actor_email,
                principal=principal,
            )
        finally:
            if isolated_db:
                try:
                    isolated_db.close()
                except Exception:
                    pass

    log_isolated = log_live
