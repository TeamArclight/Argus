import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


AUDIT_HASH_VERSION = "v1"
DOMAIN_SEPARATOR = b"ARGUS_AUDIT_EVENT_V1:"


def normalize_datetime_utc(dt: Any) -> str:
    """Normalizes any datetime or ISO string to canonical RFC 3339 / ISO 8601 UTC representation."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        # Format as strict ISO 8601 with Z suffix and microsecond precision
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    elif isinstance(dt, str):
        s = dt.strip().replace(" ", "T")
        # If naive (no Z or offset), treat as UTC
        if not s.endswith("Z") and not ("+" in s[10:] or "-" in s[10:]):
            s += "Z"
        # Parse and re-format deterministically
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        except Exception:
            return s
    raise ValueError(f"Unsupported timestamp format: {type(dt)}")


def sort_nested(val: Any) -> Any:
    """Recursively sorts dictionary keys and ensures deterministic primitive types."""
    if isinstance(val, dict):
        return {k: sort_nested(v) for k, v in sorted(val.items(), key=lambda item: str(item[0]))}
    elif isinstance(val, (list, tuple)):
        return [sort_nested(item) for item in val]
    elif isinstance(val, float):
        # Format float without trailing zero noise or scientific variance if integer
        if val.is_integer():
            return int(val)
        return val
    elif isinstance(val, (int, str, bool)) or val is None:
        return val
    else:
        # Fallback to string representation for other types
        return str(val)


def build_canonical_payload(event: Any) -> dict[str, Any]:
    """Extracts immutable fields from AuditEvent instance or dict for canonical hashing."""
    if isinstance(event, dict):
        event_id = str(event.get("id") or "")
        entity_type = str(event.get("entity_type") or "")
        entity_id = str(event.get("entity_id") or "")
        action = str(event.get("action") or "")
        actor_id = str(event.get("actor_id") or "")
        actor_role = str(event.get("actor_role") or "")
        raw_ts = event.get("timestamp")
        raw_payload = event.get("payload_json") or {}
    else:
        event_id = str(getattr(event, "id", "") or "")
        entity_type = str(getattr(event, "entity_type", "") or "")
        entity_id = str(getattr(event, "entity_id", "") or "")
        action = str(getattr(event, "action", "") or "")
        actor_id = str(getattr(event, "actor_id", "") or "")
        actor_role = str(getattr(event, "actor_role", "") or "")
        raw_ts = getattr(event, "timestamp", None)
        raw_payload = getattr(event, "payload_json", {}) or {}

    normalized_ts = normalize_datetime_utc(raw_ts)
    cleaned_payload = sort_nested(dict(raw_payload))

    canonical_dict = {
        "action": action,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "audit_hash_version": AUDIT_HASH_VERSION,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "id": event_id,
        "payload_json": cleaned_payload,
        "timestamp": normalized_ts,
    }
    return canonical_dict


def canonicalize_audit_event(event: Any) -> bytes:
    """Serializes the canonical representation of an audit event to UTF-8 bytes with domain separation."""
    canonical_dict = build_canonical_payload(event)
    canonical_json = json.dumps(
        canonical_dict,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return DOMAIN_SEPARATOR + canonical_json.encode("utf-8")


def hash_audit_event(event: Any) -> str:
    """Computes the 64-character hex-encoded SHA-256 hash of the canonical audit event."""
    canonical_bytes = canonicalize_audit_event(event)
    return hashlib.sha256(canonical_bytes).hexdigest().lower()


def event_id_to_bytes32(event_id: str) -> bytes:
    """Converts a standard UUID string (36 chars) or hex string into exactly 32 bytes for EVM contracts.
    
    The 16 raw bytes of the UUID are left-aligned into 32 bytes (padded with 16 trailing zero bytes),
    matching Solidity's `bytes32(bytes16)`.
    """
    clean_id = event_id.strip()
    try:
        parsed_uuid = uuid.UUID(clean_id)
        raw_16 = parsed_uuid.bytes
        return raw_16.ljust(32, b"\x00")
    except ValueError:
        # Fallback if event_id is not a standard UUID: SHA-256 truncated/hashed to 32 bytes
        return hashlib.sha256(clean_id.encode("utf-8")).digest()
