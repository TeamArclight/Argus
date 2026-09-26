import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound

from app.audit.canonical import (
    event_id_to_bytes32,
    hash_audit_event,
)
from app.core.config import Settings, settings
from app.models.domain import AuditEvent

logger = logging.getLogger("argus.blockchain")


# Standard ABI for ArgusAuditAnchor contract
ARGUS_AUDIT_ANCHOR_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "eventId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "eventHash", "type": "bytes32"},
        ],
        "name": "anchorAuditEvent",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "eventId", "type": "bytes32"}],
        "name": "getAuditAnchor",
        "outputs": [
            {"internalType": "bytes32", "name": "eventHash", "type": "bytes32"},
            {"internalType": "uint64", "name": "blockTimestamp", "type": "uint64"},
            {"internalType": "address", "name": "submitter", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "eventId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "eventHash", "type": "bytes32"},
        ],
        "name": "verifyAuditEvent",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "authorizedRelayers",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class BlockchainAuditService:
    """Encapsulates Web3 interactions for anchoring and verifying ARGUS audit events."""

    def __init__(self, cfg: Settings | None = None, w3_override: Web3 | None = None):
        self.cfg = cfg or settings
        self._w3_override = w3_override
        self._w3: Web3 | None = None
        self._contract = None

    def is_enabled(self) -> bool:
        """Returns True only when blockchain anchoring is explicitly enabled and fully configured."""
        return bool(
            self.cfg.BLOCKCHAIN_ENABLED
            and self.cfg.BLOCKCHAIN_RPC_URL
            and self.cfg.BLOCKCHAIN_CONTRACT_ADDRESS
            and self.cfg.BLOCKCHAIN_PRIVATE_KEY
        )

    @property
    def w3(self) -> Web3:
        if self._w3_override is not None:
            return self._w3_override
        if self._w3 is None:
            rpc_url = self.cfg.BLOCKCHAIN_RPC_URL or ""
            timeout = self.cfg.BLOCKCHAIN_TIMEOUT_SECONDS
            self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
            try:
                from web3.middleware import ExtraDataToPOAMiddleware
                self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except Exception:
                pass
        return self._w3

    @property
    def contract(self):
        if self._contract is None:
            if not self.cfg.BLOCKCHAIN_CONTRACT_ADDRESS:
                raise ValueError("BLOCKCHAIN_CONTRACT_ADDRESS is not configured.")
            import re
            m = re.search(r"(0x[0-9a-fA-F]{40})", self.cfg.BLOCKCHAIN_CONTRACT_ADDRESS, re.IGNORECASE)
            clean_addr = m.group(1) if m else self.cfg.BLOCKCHAIN_CONTRACT_ADDRESS.strip().strip("'\"").strip()
            checksum_addr = Web3.to_checksum_address(clean_addr)
            self._contract = self.w3.eth.contract(address=checksum_addr, abi=ARGUS_AUDIT_ANCHOR_ABI)
        return self._contract

    def compute_event_hash(self, event: Any) -> str:
        """Computes deterministic v1 canonical SHA-256 hash for an audit event."""
        return hash_audit_event(event)

    def get_onchain_anchor(self, event_id: str) -> tuple[str | None, int | None, str | None]:
        """Queries smart contract getAuditAnchor(bytes32).
        
        Returns:
            (event_hash_hex, block_timestamp, submitter_address)
            or (None, None, None) if not anchored.
        """
        if not self.is_enabled() and self._w3_override is None:
            return None, None, None

        b32_id = event_id_to_bytes32(event_id)
        try:
            res = self.contract.functions.getAuditAnchor(b32_id).call()
            raw_hash, block_ts, submitter = res
            if raw_hash == b"\x00" * 32 or raw_hash == "0x" + "00" * 32 or not raw_hash:
                return None, None, None

            if isinstance(raw_hash, bytes):
                hash_hex = raw_hash.hex().lower()
            else:
                hash_hex = str(raw_hash).lower().replace("0x", "")
            return hash_hex, int(block_ts), str(submitter)
        except Exception as exc:
            logger.warning("Failed to query on-chain anchor for event %s: %s", event_id, exc)
            raise

    def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        """Retrieves transaction receipt from the network."""
        if not self.is_enabled() and self._w3_override is None:
            return None
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            if receipt:
                return dict(receipt)
            return None
        except TransactionNotFound:
            return None
        except Exception as exc:
            logger.warning("Error fetching transaction receipt for %s: %s", tx_hash, exc)
            return None

    def submit_anchor(self, event_id: str, event_hash: str) -> dict[str, Any]:
        """Submits an audit event anchor to Polygon Amoy testnet.
        
        Guarantees:
        1. Queries contract first to prevent re-submitting an already anchored event.
        2. Detects hash conflicts (if already anchored with DIFFERENT hash).
        3. Signs transaction locally; private key never leaves memory.
        4. Broadcasts transaction and waits for receipt.
        """
        if not self.is_enabled() and self._w3_override is None:
            raise RuntimeError("Blockchain anchoring is disabled or not configured.")

        b32_id = event_id_to_bytes32(event_id)
        clean_hash = event_hash.strip().lower().replace("0x", "")
        if len(clean_hash) != 64:
            raise ValueError(f"Invalid event hash format (expected 64 hex chars): {clean_hash}")
        b32_hash = bytes.fromhex(clean_hash)

        # 1. Check existing on-chain state
        existing_hash, existing_ts, existing_submitter = self.get_onchain_anchor(event_id)
        if existing_hash:
            if existing_hash == clean_hash:
                logger.info("Event %s is already anchored on-chain with matching hash.", event_id)
                return {
                    "status": "CONFIRMED",
                    "event_hash": clean_hash,
                    "anchored_at": datetime.fromtimestamp(existing_ts or 0, tz=timezone.utc) if existing_ts else None,
                    "already_anchored": True,
                }
            else:
                raise ValueError(
                    f"Integrity conflict: Event {event_id} already anchored with DIFFERENT hash {existing_hash} vs {clean_hash}."
                )

        # 2. Prepare transaction
        priv_key = self.cfg.BLOCKCHAIN_PRIVATE_KEY
        if not priv_key or not priv_key.strip():
            raise RuntimeError("BLOCKCHAIN_PRIVATE_KEY is missing.")
        import re
        m = re.search(r"([0-9a-fA-F]{64})", priv_key)
        if m:
            clean_key = "0x" + m.group(1).lower()
        else:
            clean_key = priv_key.strip().strip("'\"").strip()
            if not clean_key.startswith("0x"):
                clean_key = "0x" + clean_key

        account = self.w3.eth.account.from_key(clean_key)
        sender_address = account.address

        chain_id = self.cfg.BLOCKCHAIN_CHAIN_ID
        nonce = self.w3.eth.get_transaction_count(sender_address, "pending")

        # Estimate gas or use sensible default with headroom
        try:
            gas_est = self.contract.functions.anchorAuditEvent(b32_id, b32_hash).estimate_gas({"from": sender_address})
            gas_limit = int(gas_est * 1.3)
        except Exception:
            gas_limit = 120_000

        # EIP-1559 gas calculation
        latest_block = self.w3.eth.get_block("latest")
        base_fee = latest_block.get("baseFeePerGas") or self.w3.to_wei("30", "gwei")
        try:
            network_priority_fee = self.w3.eth.max_priority_fee
            max_priority_fee = max(network_priority_fee, self.w3.to_wei("30", "gwei"))
        except Exception:
            max_priority_fee = self.w3.to_wei("30", "gwei")
        max_fee = int(base_fee * 2) + max_priority_fee

        tx_dict = self.contract.functions.anchorAuditEvent(b32_id, b32_hash).build_transaction({
            "chainId": chain_id,
            "from": sender_address,
            "nonce": nonce,
            "gas": gas_limit,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority_fee,
        })

        # 3. Sign locally and broadcast
        signed_tx = self.w3.eth.account.sign_transaction(tx_dict, private_key=clean_key)
        raw_tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hash_hex = raw_tx_hash.hex()
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex

        logger.info("Broadcasted anchor transaction %s for event %s", tx_hash_hex, event_id)

        # 4. Wait for receipt
        receipt = self.w3.eth.wait_for_transaction_receipt(raw_tx_hash, timeout=self.cfg.BLOCKCHAIN_TIMEOUT_SECONDS)
        status = receipt.get("status")
        block_number = receipt.get("blockNumber")

        if status != 1:
            raise RuntimeError(f"Anchoring transaction {tx_hash_hex} reverted on-chain (status 0).")

        return {
            "status": "CONFIRMED",
            "transaction_hash": tx_hash_hex,
            "block_number": block_number,
            "event_hash": clean_hash,
            "anchored_at": datetime.now(timezone.utc),
            "already_anchored": False,
        }

    def verify_event_integrity(self, db: Session, event_id: str) -> dict[str, Any]:
        """Performs authoritative cryptographic integrity verification for an AuditEvent.
        
        Algorithm:
        1. Loads current AuditEvent from PostgreSQL.
        2. Computes canonical SHA-256 hash `computed_hash` from current database row.
        3. Queries on-chain smart contract for `onchain_hash`.
        4. Compares `computed_hash == onchain_hash`:
           - If matched: returns VERIFIED.
           - If mismatched: returns TAMPERED.
           - If on-chain hash is absent: returns NOT_ANCHORED.
        """
        event = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
        if not event:
            return {
                "event_id": event_id,
                "integrity": "NOT_FOUND",
                "computed_hash": None,
                "onchain_hash": None,
                "transaction_hash": None,
                "details": f"AuditEvent with ID {event_id} not found in database.",
            }

        computed_hash = self.compute_event_hash(event)

        # If event is marked NOT_ANCHORED or blockchain is disabled without on-chain record
        if event.blockchain_status == "NOT_ANCHORED" and not event.blockchain_tx_hash:
            return {
                "event_id": event_id,
                "integrity": "NOT_ANCHORED",
                "computed_hash": computed_hash,
                "onchain_hash": None,
                "transaction_hash": None,
                "details": "Audit event has not been anchored to the blockchain.",
            }

        try:
            onchain_hash, block_ts, submitter = self.get_onchain_anchor(event_id)
        except Exception as exc:
            return {
                "event_id": event_id,
                "integrity": "VERIFICATION_ERROR",
                "computed_hash": computed_hash,
                "onchain_hash": None,
                "transaction_hash": event.blockchain_tx_hash,
                "details": f"Blockchain network query failed: {str(exc)}",
            }

        if not onchain_hash:
            return {
                "event_id": event_id,
                "integrity": "NOT_ANCHORED",
                "computed_hash": computed_hash,
                "onchain_hash": None,
                "transaction_hash": event.blockchain_tx_hash,
                "details": "No anchor record exists on-chain for this event ID.",
            }

        if computed_hash.lower() == onchain_hash.lower():
            return {
                "event_id": event_id,
                "integrity": "VERIFIED",
                "computed_hash": computed_hash,
                "onchain_hash": onchain_hash,
                "transaction_hash": event.blockchain_tx_hash,
                "block_number": event.blockchain_block_number,
                "anchored_at": event.anchored_at.isoformat() if event.anchored_at else None,
                "details": "Cryptographic proof verified: PostgreSQL record matches the immutable on-chain anchor.",
            }
        else:
            return {
                "event_id": event_id,
                "integrity": "TAMPERED",
                "computed_hash": computed_hash,
                "onchain_hash": onchain_hash,
                "transaction_hash": event.blockchain_tx_hash,
                "block_number": event.blockchain_block_number,
                "details": (
                    "TAMPERING DETECTED: The current database record has been altered post-anchoring. "
                    f"Computed hash {computed_hash} does not match immutable on-chain hash {onchain_hash}."
                ),
            }


_blockchain_service_instance: BlockchainAuditService | None = None


def get_blockchain_service() -> BlockchainAuditService:
    global _blockchain_service_instance
    if _blockchain_service_instance is None:
        _blockchain_service_instance = BlockchainAuditService()
    return _blockchain_service_instance
