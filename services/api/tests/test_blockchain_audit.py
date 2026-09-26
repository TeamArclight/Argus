from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from hexbytes import HexBytes

from app.audit.canonical import hash_audit_event, event_id_to_bytes32
from app.audit.logger import AuditLogger
from app.audit.policy import is_action_eligible_for_anchoring
from app.core.config import get_settings
from app.db.session import Base, engine, SessionLocal
from app.main import app
from app.models.domain import AuditEvent
from app.schemas.canonical import UserRole
from app.services.blockchain_anchor_worker import BlockchainAnchorWorker
from app.services.blockchain_service import BlockchainAuditService, get_blockchain_service
from tests.auth_helpers import get_auth_headers


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_action_eligibility_policy():
    """Verify action anchoring allowlist and exclusion behavior."""
    assert is_action_eligible_for_anchoring("CLAUSE_EVALUATED") is True
    assert is_action_eligible_for_anchoring("HUMAN_DECISION_RECORDED") is True
    assert is_action_eligible_for_anchoring("COMPLIANCE_EVALUATION_COMPLETED") is True
    assert is_action_eligible_for_anchoring("DOCUMENT_UPLOADED") is True
    assert is_action_eligible_for_anchoring("STATUTORY_PAN_VERIFICATION_COMPLETED") is True

    # Operational/heartbeat events are excluded
    assert is_action_eligible_for_anchoring("PROVIDER_HEALTH_CHECKED") is False
    assert is_action_eligible_for_anchoring("SYSTEM_METRIC_REPORTED") is False
    assert is_action_eligible_for_anchoring("UNKNOWN_EVENT") is False


def test_audit_logger_creates_event_with_hash_and_status(db_session):
    """Verify AuditLogger computes canonical hash v1 and sets initial blockchain_status."""
    with patch("app.audit.policy.settings") as mock_settings:
        mock_settings.BLOCKCHAIN_ENABLED = True
        mock_settings.BLOCKCHAIN_NETWORK = "polygon-amoy"
        mock_settings.get_blockchain_anchor_actions.return_value = {
            "COMPLIANCE_EVALUATION_COMPLETED"
        }

        # 1. Eligible event gets PENDING status and hash
        event1 = AuditLogger.create_entry(
            db_session,
            action="COMPLIANCE_EVALUATION_COMPLETED",
            entity_type="COMPLIANCE_RUN",
            entity_id="run-12345",
            actor_id="officer-01",
            actor_role="PROCUREMENT_OFFICER",
            payload={"overall_status": "COMPLIANT"},
        )
        db_session.commit()
        db_session.refresh(event1)

        assert event1.event_hash is not None
        assert len(event1.event_hash) == 64
        assert event1.blockchain_status == "PENDING"
        assert event1.audit_hash_version == "v1"

        # 2. Ineligible event gets NOT_ANCHORED status
        event2 = AuditLogger.create_entry(
            db_session,
            action="PROVIDER_HEALTH_CHECKED",
            entity_type="SYSTEM",
            entity_id="gemini-provider",
            actor_id="system",
            actor_role="SYSTEM",
            payload={"status": "UP"},
        )
        db_session.commit()
        db_session.refresh(event2)

        assert event2.event_hash is not None
        assert event2.blockchain_status == "NOT_ANCHORED"


def test_blockchain_service_submit_anchor_successful():
    """Verify submit_anchor builds, signs, broadcasts, and returns confirmation details."""
    service = BlockchainAuditService()

    event_id = str(uuid.uuid4())
    event_hash = "aa" * 32

    # Mock Web3 components
    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True
    mock_w3.eth.chain_id = 80002
    mock_w3.eth.get_transaction_count.return_value = 5
    mock_w3.eth.max_priority_fee = 30000000000
    mock_w3.eth.get_block.return_value = {"baseFeePerGas": 20000000000}
    mock_w3.eth.send_raw_transaction.return_value = HexBytes("0x" + "bb" * 32)
    mock_w3.eth.wait_for_transaction_receipt.return_value = {
        "status": 1,
        "blockNumber": 54321,
        "transactionHash": HexBytes("0x" + "bb" * 32),
        "gasUsed": 55000,
    }

    mock_account = MagicMock()
    mock_account.address = "0x9876543210987654321098765432109876543210"
    mock_signed_tx = MagicMock()
    mock_signed_tx.raw_transaction = b"fake_signed_tx"
    mock_account.sign_transaction.return_value = mock_signed_tx
    mock_w3.eth.account.from_key.return_value = mock_account

    mock_contract = MagicMock()
    # Contract: event not anchored yet
    mock_contract.functions.getAuditAnchor.return_value.call.return_value = (
        b"\x00" * 32,
        0,
        "0x0000000000000000000000000000000000000000",
    )
    mock_contract.functions.anchorAuditEvent.return_value.estimate_gas.return_value = 65000
    mock_contract.functions.anchorAuditEvent.return_value.build_transaction.return_value = {
        "to": "0x1234567890123456789012345678901234567890",
        "data": "0x",
        "gas": 80000,
        "nonce": 5,
        "chainId": 80002,
    }

    service = BlockchainAuditService(w3_override=mock_w3)
    service._contract = mock_contract

    with patch.object(service, "is_enabled", return_value=True), \
         patch.object(service.cfg, "BLOCKCHAIN_PRIVATE_KEY", "0x" + "11" * 32):

        result = service.submit_anchor(event_id, event_hash)

        assert result["status"] == "CONFIRMED"
        assert result["block_number"] == 54321
        assert result["transaction_hash"] == "0x" + "bb" * 32
        assert result["already_anchored"] is False


def test_blockchain_service_recovers_if_already_anchored():
    """Verify idempotency: if event is already anchored on-chain with identical hash, return confirmed gracefully without re-submitting."""
    event_id = str(uuid.uuid4())
    event_hash = "cc" * 32

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True

    mock_contract = MagicMock()
    # Contract already has this exact hash recorded at block 77777
    expected_hash_bytes = bytes.fromhex(event_hash)
    mock_contract.functions.getAuditAnchor.return_value.call.return_value = (
        expected_hash_bytes,
        77777,
        "0xRelayerAddress",
    )

    service = BlockchainAuditService(w3_override=mock_w3)
    service._contract = mock_contract

    with patch.object(service, "is_enabled", return_value=True):
        result = service.submit_anchor(event_id, event_hash)

        assert result["status"] == "CONFIRMED"
        assert result["already_anchored"] is True


def test_blockchain_service_conflict_detected():
    """Verify hash conflict detection: if on-chain hash does not match event hash, raise error."""
    event_id = str(uuid.uuid4())
    event_hash = "dd" * 32

    mock_w3 = MagicMock()
    mock_w3.is_connected.return_value = True

    mock_contract = MagicMock()
    # Contract has DIFFERENT hash recorded for this event ID
    mock_contract.functions.getAuditAnchor.return_value.call.return_value = (
        b"\xff" * 32,
        99999,
        "0xOtherRelayer",
    )

    service = BlockchainAuditService(w3_override=mock_w3)
    service._contract = mock_contract

    with patch.object(service, "is_enabled", return_value=True):
        with pytest.raises(ValueError, match="Integrity conflict"):
            service.submit_anchor(event_id, event_hash)


def test_blockchain_service_verify_integrity(db_session):
    """Verify verify_event_integrity distinguishes VERIFIED, TAMPERED, and NOT_ANCHORED."""
    event = AuditEvent(
        id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        action="STATUTORY_PAN_VERIFICATION_COMPLETED",
        entity_type="STATUTORY_VERIFICATION",
        entity_id="pan-123",
        actor_id="verifier-01",
        actor_role="SYSTEM",
        payload_json={"status": "AUTHENTIC"},
        blockchain_status="CONFIRMED",
        blockchain_block_number=45000,
        blockchain_tx_hash="0x" + "ee" * 32,
        audit_hash_version="v1",
    )
    event.event_hash = hash_audit_event(event)
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    service = BlockchainAuditService()

    # 1. Authentic match -> VERIFIED
    with patch.object(service, "get_onchain_anchor", return_value=(event.event_hash, 45000, "0xRelayer")):
        res = service.verify_event_integrity(db_session, event.id)
        assert res["integrity"] == "VERIFIED"
        assert res["computed_hash"] == event.event_hash
        assert res["onchain_hash"] == event.event_hash
        assert res["block_number"] == 45000

    # 2. Tampered record in DB (e.g. action modified directly) -> TAMPERED
    event.action = "FRAUDULENT_ACTION_TAMPERED"
    db_session.commit()
    db_session.refresh(event)

    with patch.object(service, "get_onchain_anchor", return_value=(event.event_hash, 45000, "0xRelayer")):
        res = service.verify_event_integrity(db_session, event.id)
        assert res["integrity"] == "TAMPERED"
        assert res["computed_hash"] != event.event_hash
        assert res["onchain_hash"] == event.event_hash

    # 3. Unanchored event -> NOT_ANCHORED
    unanchored_id = str(uuid.uuid4())
    unanchored_event = AuditEvent(
        id=unanchored_id,
        timestamp=datetime.now(timezone.utc),
        action="SYSTEM_PING",
        entity_type="SYSTEM",
        entity_id="sys-ping",
        actor_id="system",
        actor_role="SYSTEM",
        blockchain_status="NOT_ANCHORED",
    )
    db_session.add(unanchored_event)
    db_session.commit()

    res_unanchored = service.verify_event_integrity(db_session, unanchored_id)
    assert res_unanchored["integrity"] == "NOT_ANCHORED"


def test_blockchain_anchor_worker_sweep_and_retry(db_session):
    """Verify BlockchainAnchorWorker sweeps pending events and applies exponential backoff on errors."""
    event_id = str(uuid.uuid4())
    event = AuditEvent(
        id=event_id,
        timestamp=datetime.now(timezone.utc),
        action="CLAUSE_EVALUATED",
        entity_type="CLAUSE",
        entity_id="clause-1",
        actor_id="system",
        actor_role="SYSTEM",
        payload_json={"status": "PASS"},
        blockchain_status="PENDING",
        blockchain_retry_count=0,
    )
    event.event_hash = hash_audit_event(event)
    db_session.add(event)
    db_session.commit()
    db_session.close()

    worker = BlockchainAnchorWorker()

    # Case A: Service throws RPC exception -> worker increments retry and sets next_retry_at
    with patch.object(worker.service, "is_enabled", return_value=True), \
         patch.object(worker.service, "submit_anchor", side_effect=Exception("RPC timeout")):

        confirmed = worker.process_batch(limit=5)
        assert confirmed == 0

        db = SessionLocal()
        refreshed = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
        assert refreshed.blockchain_retry_count == 1
        assert refreshed.blockchain_status == "FAILED"  # marked FAILED but retryable via next_retry_at
        assert refreshed.blockchain_next_retry_at is not None
        assert "RPC timeout" in refreshed.blockchain_error
        db.close()

    # Case B: Success on subsequent attempt
    with patch.object(worker.service, "is_enabled", return_value=True), \
         patch.object(worker.service, "submit_anchor", return_value={
             "status": "CONFIRMED",
             "transaction_hash": "0x12345678",
             "block_number": 98765,
             "anchored_at": datetime.now(timezone.utc),
         }):

        # Set next_retry_at in the past so it's picked up
        db = SessionLocal()
        ev = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
        ev.blockchain_next_retry_at = datetime.now(timezone.utc)
        db.commit()
        db.close()

        confirmed = worker.process_batch(limit=5)
        assert confirmed == 1

        db = SessionLocal()
        final_ev = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
        assert final_ev.blockchain_status == "CONFIRMED"
        assert final_ev.blockchain_block_number == 98765
        assert final_ev.blockchain_tx_hash == "0x12345678"
        db.close()


def test_api_audit_blockchain_status_and_verify():
    """Verify GET /api/v1/audit/{event_id}/blockchain and POST /api/v1/audit/{event_id}/verify."""
    client = TestClient(app)

    # Create event in DB
    db = SessionLocal()
    event_id = str(uuid.uuid4())
    event = AuditEvent(
        id=event_id,
        timestamp=datetime.now(timezone.utc),
        action="HUMAN_DECISION_RECORDED",
        entity_type="DECISION",
        entity_id="dec-endpoint-test",
        actor_id="officer-endpoint",
        actor_role="PROCUREMENT_OFFICER",
        payload_json={"note": "valid"},
        blockchain_status="CONFIRMED",
        blockchain_network="polygon-amoy",
        blockchain_tx_hash="0x" + "ff" * 32,
        blockchain_block_number=100001,
        anchored_at=datetime.now(timezone.utc),
        audit_hash_version="v1",
    )
    event.event_hash = hash_audit_event(event)
    target_event_hash = event.event_hash
    db.add(event)
    db.commit()
    db.close()

    # 1. Unauthenticated request rejected
    res_unauth = client.get(f"/api/v1/audit/{event_id}/blockchain")
    assert res_unauth.status_code == 401

    # 2. Authenticated auditor can view blockchain status
    headers = get_auth_headers(role=UserRole.AUDITOR)
    res = client.get(f"/api/v1/audit/{event_id}/blockchain", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["event_id"] == str(event_id)
    assert data["blockchain_status"] == "CONFIRMED"
    assert data["network"] == "polygon-amoy"
    assert data["block_number"] == 100001
    assert data["transaction_hash"] == "0x" + "ff" * 32
    assert "amoy.polygonscan.com" in data["explorer_url"]

    # 3. Post verify endpoint
    with patch.object(
        BlockchainAuditService,
        "get_onchain_anchor",
        return_value=(target_event_hash, 100001, "0xRelayer"),
    ):
        res_verify = client.post(f"/api/v1/audit/{event_id}/verify", headers=headers)
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["integrity"] == "VERIFIED"
        assert verify_data["computed_hash"] == target_event_hash
        assert verify_data["onchain_hash"] == target_event_hash
        assert verify_data["block_number"] == 100001
