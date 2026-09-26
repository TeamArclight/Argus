import copy
from datetime import datetime, timezone
import pytest
from app.audit.canonical import (
    AUDIT_HASH_VERSION,
    DOMAIN_SEPARATOR,
    build_canonical_payload,
    canonicalize_audit_event,
    event_id_to_bytes32,
    hash_audit_event,
    normalize_datetime_utc,
)


def test_normalize_datetime_utc():
    # Naive datetime
    dt_naive = datetime(2026, 9, 19, 15, 30, 45, 123456)
    res_naive = normalize_datetime_utc(dt_naive)
    assert res_naive == "2026-09-19T15:30:45.123456Z"

    # Timezone-aware UTC
    dt_utc = datetime(2026, 9, 19, 15, 30, 45, 123456, tzinfo=timezone.utc)
    res_utc = normalize_datetime_utc(dt_utc)
    assert res_utc == "2026-09-19T15:30:45.123456Z"

    # String with space
    res_str = normalize_datetime_utc("2026-09-19 15:30:45.123456")
    assert res_str == "2026-09-19T15:30:45.123456Z"


def test_same_event_same_hash():
    event = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "actor_id": "OFFICER-1",
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "payload_json": {"status": "PASS", "score": 98.5, "clauses": ["1.1", "2.3"]},
    }
    hash1 = hash_audit_event(event)
    hash2 = hash_audit_event(event)
    assert len(hash1) == 64
    assert hash1 == hash2


def test_reordered_json_same_hash():
    event1 = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "actor_id": "OFFICER-1",
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "payload_json": {"z": 1, "a": {"sub_z": 9, "sub_a": 10}, "b": [1, 2]},
    }
    event2 = {
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "payload_json": {"b": [1, 2], "a": {"sub_a": 10, "sub_z": 9}, "z": 1},
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "entity_id": "b123",
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "actor_id": "OFFICER-1",
        "entity_type": "BIDDER",
    }
    assert hash_audit_event(event1) == hash_audit_event(event2)


def test_changed_action_different_hash():
    event = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "actor_id": "OFFICER-1",
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "payload_json": {"status": "PASS"},
    }
    event_tampered = copy.deepcopy(event)
    event_tampered["action"] = "COMPLIANCE_EVALUATION_FAILED"

    assert hash_audit_event(event) != hash_audit_event(event_tampered)


def test_changed_payload_different_hash():
    event = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "actor_id": "OFFICER-1",
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "payload_json": {"status": "PASS"},
    }
    event_tampered = copy.deepcopy(event)
    event_tampered["payload_json"]["status"] = "FAIL"

    assert hash_audit_event(event) != hash_audit_event(event_tampered)


def test_blockchain_metadata_changes_do_not_alter_hash():
    base_event = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "COMPLIANCE_EVALUATION_COMPLETED",
        "actor_id": "OFFICER-1",
        "actor_role": "PROCUREMENT_OFFICER",
        "timestamp": "2026-09-19T15:30:45.123456Z",
        "payload_json": {"status": "PASS"},
    }
    initial_hash = hash_audit_event(base_event)

    # Add blockchain metadata fields
    event_with_bc = copy.deepcopy(base_event)
    event_with_bc["blockchain_status"] = "CONFIRMED"
    event_with_bc["blockchain_tx_hash"] = "0x" + "a" * 64
    event_with_bc["blockchain_block_number"] = 123456
    event_with_bc["blockchain_network"] = "polygon-amoy"
    event_with_bc["anchored_at"] = "2026-09-19T15:31:00Z"
    event_with_bc["blockchain_error"] = None
    event_with_bc["blockchain_retry_count"] = 0

    assert hash_audit_event(event_with_bc) == initial_hash


def test_event_id_to_bytes32():
    uuid_str = "69a0fa6b-e49e-4318-850a-615c44578bea"
    b32 = event_id_to_bytes32(uuid_str)
    assert len(b32) == 32
    # First 16 bytes must match raw UUID bytes
    import uuid
    expected_16 = uuid.UUID(uuid_str).bytes
    assert b32[:16] == expected_16
    # Last 16 bytes must be zeros
    assert b32[16:] == b"\x00" * 16

    # Same input produces exact same bytes
    assert event_id_to_bytes32(uuid_str) == b32


def test_canonical_domain_prefix():
    event = {
        "id": "69a0fa6b-e49e-4318-850a-615c44578bea",
        "entity_type": "BIDDER",
        "entity_id": "b123",
        "action": "TEST",
        "actor_id": "U1",
        "actor_role": "ADMIN",
        "timestamp": "2026-09-19T15:30:45.000000Z",
        "payload_json": {},
    }
    raw_bytes = canonicalize_audit_event(event)
    assert raw_bytes.startswith(DOMAIN_SEPARATOR)
