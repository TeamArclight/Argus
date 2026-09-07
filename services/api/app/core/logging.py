from datetime import datetime, timezone
import json
import logging
import re
from typing import Any
from app.core.middleware import get_request_id

# Regex rules for redacting sensitive fields (PAN, GSTIN, JWT tokens, Bearer tokens, secrets)
PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")
GSTIN_REGEX = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b")
BEARER_TOKEN_REGEX = re.compile(r"Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*", re.IGNORECASE)
JWT_REGEX = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

SENSITIVE_KEYS = {
    "password", "secret", "token", "jwt", "api_key", "authorization",
    "access_token", "private_key", "argus_jwt_secret"
}


def redact_sensitive_string(text: str) -> str:
    """Redacts PAN, GSTIN, Bearer tokens, and JWT strings from log text."""
    if not text or not isinstance(text, str):
        return text
    text = BEARER_TOKEN_REGEX.sub("Bearer [REDACTED]", text)
    text = JWT_REGEX.sub("[REDACTED_JWT]", text)
    text = PAN_REGEX.sub("[REDACTED_PAN]", text)
    text = GSTIN_REGEX.sub("[REDACTED_GSTIN]", text)
    return text


def sanitize_log_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitizes dictionary keys and values for safe structured logging."""
    sanitized: dict[str, Any] = {}
    for key, val in data.items():
        key_str = str(key)
        if any(sens in key_str.lower() for sens in SENSITIVE_KEYS):
            sanitized[key_str] = "[REDACTED]"
        elif isinstance(val, dict):
            sanitized[key_str] = sanitize_log_dict(val)
        elif isinstance(val, list):
            sanitized[key_str] = [
                sanitize_log_dict(v) if isinstance(v, dict)
                else (redact_sensitive_string(v) if isinstance(v, str) else v)
                for v in val
            ]
        elif isinstance(val, str):
            sanitized[key_str] = redact_sensitive_string(val)
        else:
            sanitized[key_str] = val
    return sanitized


class StructuredJSONFormatter(logging.Formatter):
    """Structured JSON formatter with allowlisted fields and request correlation."""

    def format(self, record: logging.LogRecord) -> str:
        req_id = get_request_id() or getattr(record, "request_id", "")
        
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": req_id,
            "event": record.getMessage(),
            "component": getattr(record, "component", record.module),
        }

        # Include allowlisted diagnostic fields if present on record
        for attr in ("duration_ms", "resource_id", "status_code", "reason_code", "entity_type"):
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        if record.exc_text:
            # Redact sensitive details from exception tracebacks
            log_entry["exception"] = redact_sensitive_string(record.exc_text)

        sanitized_entry = sanitize_log_dict(log_entry)
        return json.dumps(sanitized_entry)


def get_logger(name: str = "argus.api") -> logging.Logger:
    """Returns configured structured logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
