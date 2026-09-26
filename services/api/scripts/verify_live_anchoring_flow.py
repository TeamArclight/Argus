"""Live end-to-end verification of the ARGUS audit trail blockchain anchoring flow on Polygon Amoy.

Steps performed:
1. Initialize DB and create a real audit event via AuditLogger (action: COMPLIANCE_EVALUATION_COMPLETED).
2. Commit transaction (PostgreSQL / SQLite).
3. Confirm event is created with blockchain_status == PENDING and has canonical SHA-256 hash.
4. Run blockchain worker process_pending_audit_events() to pick up and anchor the event on Polygon Amoy.
5. Confirm DB record is updated to ANCHORED with tx_hash and block_number.
6. Retrieve on-chain proof via BlockchainService.verify_event_integrity().
7. Assert cryptographic proof matches on-chain state.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.logger import AuditLogger
from app.core.config import get_settings
from app.db.session import Base, SessionLocal, engine
from app.models.domain import AuditEvent
from app.services.blockchain_anchor_worker import BlockchainAnchorWorker
from app.services.blockchain_service import get_blockchain_service


def main():
    print("=" * 65)
    print(" ARGUS LIVE END-TO-END BLOCKCHAIN ANCHORING TEST")
    print("=" * 65)

    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    blockchain_service = get_blockchain_service()
    worker = BlockchainAnchorWorker(cfg=settings, service=blockchain_service)

    print(f"Contract:  {settings.BLOCKCHAIN_CONTRACT_ADDRESS}")
    print(f"RPC URL:   {settings.BLOCKCHAIN_RPC_URL}")
    print(f"Chain ID:  {settings.BLOCKCHAIN_CHAIN_ID}")
    print("-" * 65)

    # 1. Create a real audit event via AuditLogger
    tender_id = str(uuid.uuid4())
    print("\n[Step 1] Creating audit event via AuditLogger...")
    event = AuditLogger.create_entry(
        db=db,
        action="COMPLIANCE_EVALUATION_COMPLETED",
        entity_type="tender",
        entity_id=tender_id,
        actor_id="system:argus-evaluator-engine",
        actor_role="SYSTEM",
        payload={"bid_score": 94.5, "passed_clauses": 18, "failed_clauses": 0},
    )
    db.commit()
    db.refresh(event)

    print(f"Event ID:           {event.id}")
    print(f"Canonical Hash:     {event.event_hash}")
    print(f"Initial Status:     {event.blockchain_status}")
    assert event.blockchain_status == "PENDING", f"Expected PENDING, got {event.blockchain_status}"
    assert event.event_hash is not None, "Canonical hash must be computed"

    # 2. Trigger worker sweep
    print("\n[Step 2] Executing worker sweep (worker.process_batch)...")
    processed = worker.process_batch(limit=1)
    print(f"Worker processed:   {processed} event(s)")
    assert processed >= 1, "Expected at least 1 event to be processed"

    # 3. Inspect updated database record
    db.refresh(event)
    print(f"\n[Step 3] Inspecting updated DB record:")
    print(f"Updated Status:     {event.blockchain_status}")
    print(f"Tx Hash:            {event.blockchain_tx_hash}")
    print(f"Block Number:       {event.blockchain_block_number}")
    print(f"Anchored At:        {event.anchored_at}")

    assert event.blockchain_status == "CONFIRMED", f"Expected CONFIRMED, got {event.blockchain_status}"
    assert event.blockchain_tx_hash is not None, "Transaction hash must not be None"
    assert event.blockchain_block_number is not None, "Block number must not be None"

    # 4. Verify cryptographic integrity against on-chain smart contract
    print("\n[Step 4] Querying live on-chain smart contract via verify_event_integrity()...")
    verification = blockchain_service.verify_event_integrity(db=db, event_id=event.id)
    print(f"Integrity Status:   {verification['integrity']}")
    print(f"Computed Hash:      {verification['computed_hash']}")
    print(f"On-Chain Hash:      {verification['onchain_hash']}")
    print(f"Tx Hash:            {verification['transaction_hash']}")
    print(f"Block Number:       {verification['block_number']}")

    assert verification["integrity"] == "VERIFIED", f"Expected VERIFIED, got {verification['integrity']}"
    assert verification["computed_hash"] == verification["onchain_hash"], "Computed hash must match on-chain hash"
    assert verification["onchain_hash"] == event.event_hash, "On-chain hash must match event canonical hash"

    explorer_url = f"{settings.BLOCKCHAIN_EXPLORER_URL}/tx/{verification['transaction_hash']}"
    print("-" * 65)
    print("[SUCCESS] FULL LIVE END-TO-END FLOW VERIFIED ON POLYGON AMOY!")
    print(f"View Transaction:   {explorer_url}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
