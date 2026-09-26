# ARGUS Blockchain-Backed Audit Trail Architecture & Tamper Verification

## 1. Architectural Overview & Security Invariants

ARGUS maintains an immutable, cryptographically verifiable audit trail for government procurement compliance evaluations. To guarantee that audit records cannot be modified after the fact—even by a database administrator or malicious actor with full PostgreSQL write access—ARGUS anchors cryptographic SHA-256 digests of audit events to the **Polygon Amoy testnet (Chain ID: 80002)** via a lightweight smart contract (`ArgusAuditAnchor.sol`).

### Core Security Invariants:
1. **Never Block Domain Transactions**: All procurement and compliance operations commit to PostgreSQL immediately. Blockchain anchoring is 100% asynchronous and performed post-commit.
2. **Transaction & Rollback Safety**: Audit events are only eligible for blockchain submission *after* the PostgreSQL transaction containing them has successfully committed. Rolled-back operations leave zero on-chain residue.
3. **Zero Sensitive Data On-Chain**: No PII, bidder company secrets, PANs, GSTINs, tender texts, or evaluation payloads are sent to the blockchain. Only `(bytes32 eventId, bytes32 eventHash)` are recorded on-chain.
4. **Distinction between `CONFIRMED` and `VERIFIED`**:
   - `CONFIRMED`: Indicates that a transaction receipt exists on-chain for the audit record.
   - `VERIFIED`: Indicates that the cryptographic digest was freshly recomputed from current PostgreSQL row state and matched exactly against the immutable on-chain hash. The UI displays `✓ Integrity Verified` only upon real-time hash comparison.

```
Business Operation (e.g. Compliance Run / Officer Decision)
       ↓
AuditLogger.create_entry()
   • Computes deterministic SHA-256 hash (v1)
   • Sets blockchain_status = "PENDING" (if eligible) or "NOT_ANCHORED"
       ↓
PostgreSQL Transaction COMMITTED
       ↓
BlockchainAnchorWorker (Daemon loop every 5s)
   • Locks pending/retryable rows via with_for_update(skip_locked=True)
   • Signs transaction locally using relayer private key
   • Submits anchorAuditEvent(eventId, eventHash) to Polygon Amoy
   • Updates blockchain_status = "CONFIRMED", block_number, tx_hash
       ↓
Auditor / Frontend Real-Time Integrity Verification
   • POST /api/v1/audit/{id}/verify
   • Recomputes canonical hash on current DB row
   • Compares with on-chain anchor at block #
   • Verdict: VERIFIED | TAMPERED | NOT_ANCHORED
```

---

## 2. Canonical Hashing Specification (v1)

- **Version Identifier**: `v1`
- **Domain Separator**: `ARGUS_AUDIT_EVENT_V1:`
- **Hash Function**: Standard SHA-256 (32 bytes / 64 hex characters)

### Fields Included in Canonical Payload:
- `id`: AuditEvent UUID string
- `timestamp`: UTC ISO 8601 string (`YYYY-MM-DDTHH:MM:SS.ffffffZ`)
- `action`: Exact action string (e.g. `COMPLIANCE_EVALUATION_COMPLETED`)
- `entity_type`: Entity type string
- `entity_id`: Entity ID string
- `actor_id`: Principal actor identifier
- `actor_role`: Role identifier
- `payload_json`: Deterministic JSON string with keys sorted recursively and whitespace eliminated (`separators=(',', ':')`)

### Fields Strictly Excluded:
Mutable blockchain anchoring fields (`event_hash`, `blockchain_status`, `blockchain_network`, `blockchain_tx_hash`, `blockchain_block_number`, `anchored_at`, `blockchain_error`, `blockchain_retry_count`, `blockchain_next_retry_at`) are excluded to preserve hash immutability before and after anchoring.

---

## 3. Smart Contract (`contracts/ArgusAuditAnchor.sol`)

- **EVM Target**: Solidity `^0.8.20`
- **Network**: Polygon Amoy Testnet (Chain ID: `80002`)
- **Address & ABI**: Available in `contracts/ArgusAuditAnchor.json`

### Core State & Interface:
```solidity
struct AnchorRecord {
    bytes32 eventHash;
    uint64 blockTimestamp;
    address anchoredBy;
}

mapping(bytes32 => AnchorRecord) private _anchors;

function anchorAuditEvent(bytes32 eventId, bytes32 eventHash) external onlyRelayer;
function getAuditAnchor(bytes32 eventId) external view returns (bytes32, uint64, address);
function verifyAuditEvent(bytes32 eventId, bytes32 eventHash) external view returns (bool);
```

### Protection Guarantees:
- **Zero-check**: Rejects `eventId == 0` or `eventHash == 0`.
- **Relayer Control**: Only authorized relayers (or the contract owner) can submit anchors.
- **Write-Once Immutability**: If an `eventId` is already anchored, subsequent attempts revert with `EventAlreadyAnchored` unless the hash matches identically.

---

## 4. Asynchronous Pipeline & Worker Safety

The `BlockchainAnchorWorker` runs in a background thread or daemon worker process:
- **Selective Anchoring Policy**: Only high-value audit actions (e.g., `COMPLIANCE_EVALUATION_COMPLETED`, `HUMAN_DECISION_RECORDED`, `CLAUSE_EVALUATED`, `STATUTORY_PAN_VERIFICATION_COMPLETED`, `DOCUMENT_UPLOADED`) are anchored. Operational health checks (`PROVIDER_HEALTH_CHECKED`) are marked `NOT_ANCHORED`.
- **Bounded Exponential Backoff**: Upon RPC errors or network congestion, retries occur at intervals of `[30s, 60s, 120s, 240s, 480s]`. After 5 failed attempts, the event is marked `FAILED` with details logged in `blockchain_error`.
- **Row-Level Concurrency Control**: Uses `with_for_update(skip_locked=True)` on PostgreSQL so multiple worker threads do not compete or double-spend nonces.

---

## 5. Verification Protocol & API

### Endpoints:
1. `GET /api/v1/audit/{event_id}/blockchain`
   - Returns on-chain anchor metadata, network, block number, transaction hash, and PolygonScan Amoy explorer deep-link.
2. `POST /api/v1/audit/{event_id}/verify`
   - Dynamically recomputes the SHA-256 canonical hash from the current PostgreSQL row.
   - Reads the immutable anchor from Polygon Amoy.
   - Returns a structured integrity response:
     - `VERIFIED`: Recomputed hash matches immutable on-chain record. Zero tampering.
     - `TAMPERED`: Current PostgreSQL row differs from on-chain anchor. Out-of-band database modification detected.
     - `NOT_ANCHORED`: Event not scheduled or eligible for on-chain anchoring.
     - `VERIFICATION_ERROR`: RPC or node connectivity failure.

---

## 6. Tamper Simulation Tool (`demo_blockchain_tamper.py`)

A demonstration script is provided at `services/api/scripts/demo_blockchain_tamper.py`:
- **Strict Safety Guards**: Refuses to run if `APP_ENV` is set to `production`, `prod`, or if the database URL points to a cloud host (`rds`, `cloudsql`, `amazonaws.com`, `neon.tech`, etc.).
- **Usage**:
  ```bash
  # View integrity verification of an event
  python scripts/demo_blockchain_tamper.py --event-id <UUID> --verify-only

  # Simulate unauthorized modification of PostgreSQL row
  python scripts/demo_blockchain_tamper.py --event-id <UUID> --field action --new-value FRAUDULENT_ACTION

  # Restore original value
  python scripts/demo_blockchain_tamper.py --event-id <UUID> --field action --restore ORIGINAL_ACTION
  ```

---

## 7. Frontend Integration

1. **Audit Event Detail Drawer (`AuditEventDetailDrawer.tsx`)**:
   - Status badge indicating on-chain status (`POLYGON AMOY`, `PENDING`, `SUBMITTED`, `FAILED`, `NOT ANCHORED`).
   - Detailed Provenance Card: Canonical SHA-256 digest, PolygonScan Amoy link, block number, timestamp.
   - Interactive **"Verify Integrity"** button with real-time verification and stateful verdict banner.
2. **Audit Event Table (`workspace/audit/page.tsx`)**:
   - Visual chip in the Status column indicating whether each audit record is anchored on Polygon Amoy.
