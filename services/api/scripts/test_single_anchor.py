"""Perform a controlled single test anchor on Polygon Amoy.

Verifies:
1. anchorAuditEvent(eventId, eventHash)
2. receipt.status == 1
3. getAuditAnchor(eventId) returns matching hash
4. verifyAuditEvent(eventId, eventHash) returns True
"""

from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from web3 import Web3

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.canonical import event_id_to_bytes32
from app.core.config import get_settings
from app.services.blockchain_service import ARGUS_AUDIT_ANCHOR_ABI


def main():
    settings = get_settings()

    priv_key = os.getenv("BLOCKCHAIN_PRIVATE_KEY") or settings.BLOCKCHAIN_PRIVATE_KEY
    if not priv_key or not priv_key.strip():
        sys.stderr.write("\n[ERROR] BLOCKCHAIN_PRIVATE_KEY is not set.\n\n")
        sys.exit(1)

    clean_key = priv_key.strip()
    if not clean_key.startswith("0x"):
        clean_key = "0x" + clean_key

    contract_addr = os.getenv("BLOCKCHAIN_CONTRACT_ADDRESS") or settings.BLOCKCHAIN_CONTRACT_ADDRESS
    if not contract_addr or not contract_addr.strip():
        sys.stderr.write("\n[ERROR] BLOCKCHAIN_CONTRACT_ADDRESS is not set.\n\n")
        sys.exit(1)

    contract_addr = Web3.to_checksum_address(contract_addr.strip())

    rpc_url = (
        os.getenv("BLOCKCHAIN_RPC_URL")
        or settings.BLOCKCHAIN_RPC_URL
        or "https://polygon-amoy.drpc.org"
    )
    expected_chain_id = int(os.getenv("BLOCKCHAIN_CHAIN_ID") or settings.BLOCKCHAIN_CHAIN_ID or 80002)

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except Exception:
        pass

    if not w3.is_connected():
        sys.stderr.write(f"\n[ERROR] Unable to connect to RPC endpoint: {rpc_url}\n")
        sys.exit(1)

    actual_chain_id = w3.eth.chain_id
    if actual_chain_id != expected_chain_id:
        sys.stderr.write(f"\n[ERROR] Chain ID mismatch: {actual_chain_id} vs expected {expected_chain_id}\n")
        sys.exit(1)

    account = w3.eth.account.from_key(clean_key)
    relayer_address = account.address

    contract = w3.eth.contract(address=contract_addr, abi=ARGUS_AUDIT_ANCHOR_ABI)

    # 1. Generate test event ID and test SHA-256 hash
    test_uuid = str(uuid.uuid4())
    b32_event_id = event_id_to_bytes32(test_uuid)

    test_payload = f"ARGUS_TEST_EVENT_{test_uuid}_{datetime.now(timezone.utc).isoformat()}"
    test_hash_hex = hashlib.sha256(test_payload.encode("utf-8")).hexdigest()
    b32_event_hash = bytes.fromhex(test_hash_hex)

    print("\n" + "=" * 65)
    print(" ARGUS CONTROLLED TEST ANCHOR ON POLYGON AMOY")
    print("=" * 65)
    print(f"Contract Address:   {contract_addr}")
    print(f"Relayer Address:    {relayer_address}")
    print(f"Test Event ID:      {test_uuid}")
    print(f"Test Event Hash:    {test_hash_hex}")
    print("-" * 65)

    nonce = w3.eth.get_transaction_count(relayer_address, "pending")
    latest_block = w3.eth.get_block("latest")
    base_fee = latest_block.get("baseFeePerGas") or w3.to_wei("30", "gwei")
    try:
        network_priority_fee = w3.eth.max_priority_fee
        max_priority_fee = max(network_priority_fee, w3.to_wei("30", "gwei"))
    except Exception:
        max_priority_fee = w3.to_wei("30", "gwei")
    max_fee = int(base_fee * 2) + max_priority_fee

    tx = contract.functions.anchorAuditEvent(b32_event_id, b32_event_hash).build_transaction({
        "from": relayer_address,
        "nonce": nonce,
        "chainId": actual_chain_id,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority_fee,
    })

    try:
        gas_est = contract.functions.anchorAuditEvent(b32_event_id, b32_event_hash).estimate_gas({"from": relayer_address})
        tx["gas"] = int(gas_est * 1.3)
    except Exception:
        tx["gas"] = 120_000

    print("Signing and submitting anchor transaction...")
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = "0x" + tx_hash.hex()
    print(f"Transaction Hash:   {tx_hash_hex}")
    print("Waiting for on-chain confirmation...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.get("status") != 1:
        sys.stderr.write(f"\n[FATAL] Transaction reverted on-chain (tx: {tx_hash_hex})\n")
        sys.exit(1)

    block_number = receipt.get("blockNumber")
    print(f"Confirmed in Block: #{block_number}")

    # Verify on-chain anchor retrieval
    print("\nRetrieving on-chain anchor via getAuditAnchor()...")
    onchain_anchor = contract.functions.getAuditAnchor(b32_event_id).call()
    retrieved_hash_bytes = onchain_anchor[0]
    retrieved_ts = onchain_anchor[1]
    retrieved_submitter = onchain_anchor[2]
    retrieved_hash_hex = retrieved_hash_bytes.hex().lower()

    print(f"Retrieved Hash:     {retrieved_hash_hex}")
    print(f"On-Chain Timestamp: {datetime.fromtimestamp(retrieved_ts, tz=timezone.utc)}")
    print(f"Submitter:          {retrieved_submitter}")

    assert retrieved_hash_hex == test_hash_hex, f"Hash mismatch: {retrieved_hash_hex} vs {test_hash_hex}"

    print("Verifying via verifyAuditEvent()...")
    is_valid = contract.functions.verifyAuditEvent(b32_event_id, b32_event_hash).call()
    assert is_valid is True, "verifyAuditEvent returned False!"

    print("-" * 65)
    print("[SUCCESS] PROOF VERIFIED: Controlled test anchor successfully confirmed and verified!")
    print(f"PolygonScan Link:   https://amoy.polygonscan.com/tx/{tx_hash_hex}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
