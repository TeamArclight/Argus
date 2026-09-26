"""Deploy ArgusAuditAnchor smart contract to Polygon Amoy testnet.

Uses web3.py with compiled ABI and bytecode.
Outputs: Network, Chain ID, Deployer address, Contract address, Tx hash, Block number.
NEVER outputs or logs the private key.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from web3 import Web3

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings


def load_contract_artifacts() -> tuple[list, str]:
    """Loads ABI and bytecode from contracts/ArgusAuditAnchor.json."""
    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    artifact_path = root_dir / "contracts" / "ArgusAuditAnchor.json"
    if not artifact_path.exists():
        sys.stderr.write(f"\n[ERROR] Contract artifact not found at {artifact_path}\n")
        sys.exit(1)

    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    abi = data.get("abi")
    bytecode = data.get("bytecode")

    if not abi or not bytecode:
        sys.stderr.write(f"\n[ERROR] Incomplete artifact in {artifact_path} (missing abi or bytecode)\n")
        sys.exit(1)

    return abi, bytecode


def main():
    settings = get_settings()

    priv_key = os.getenv("BLOCKCHAIN_PRIVATE_KEY") or settings.BLOCKCHAIN_PRIVATE_KEY
    if not priv_key or not priv_key.strip():
        sys.stderr.write(
            "\n[ERROR] BLOCKCHAIN_PRIVATE_KEY is not set.\n"
            "Please set BLOCKCHAIN_PRIVATE_KEY in your environment before running deployment.\n"
            "Example (PowerShell): $env:BLOCKCHAIN_PRIVATE_KEY=\"0x...\"\n"
            "Example (bash):       export BLOCKCHAIN_PRIVATE_KEY=\"0x...\"\n\n"
        )
        sys.exit(1)

    clean_key = priv_key.strip()
    if not clean_key.startswith("0x"):
        clean_key = "0x" + clean_key

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
        sys.stderr.write(
            f"\n[ERROR] Chain ID mismatch: RPC returned {actual_chain_id}, expected {expected_chain_id} (Polygon Amoy).\n"
            "Aborting deployment to prevent deploying to an unintended network.\n"
        )
        sys.exit(1)

    account = w3.eth.account.from_key(clean_key)
    deployer_address = account.address
    balance_wei = w3.eth.get_balance(deployer_address)
    balance_pol = w3.from_wei(balance_wei, "ether")

    min_required_wei = w3.to_wei("0.02", "ether")
    if balance_wei < min_required_wei:
        sys.stderr.write(
            f"\n[ERROR] Insufficient balance for deployment: {balance_pol:.4f} POL.\n"
            f"Please fund deployer wallet {deployer_address} with at least 0.05 test POL from:\n"
            "https://faucet.polygon.technology/\n\n"
        )
        sys.exit(1)

    abi, bytecode = load_contract_artifacts()
    contract_factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    nonce = w3.eth.get_transaction_count(deployer_address, "pending")
    latest_block = w3.eth.get_block("latest")
    base_fee = latest_block.get("baseFeePerGas") or w3.to_wei("30", "gwei")
    try:
        network_priority_fee = w3.eth.max_priority_fee
        max_priority_fee = max(network_priority_fee, w3.to_wei("30", "gwei"))
    except Exception:
        max_priority_fee = w3.to_wei("30", "gwei")
    max_fee = int(base_fee * 2) + max_priority_fee

    print("\nPreparing deployment transaction...")
    construct_tx = contract_factory.constructor().build_transaction({
        "from": deployer_address,
        "nonce": nonce,
        "chainId": actual_chain_id,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority_fee,
    })

    # Estimate gas with safety margin
    try:
        gas_est = w3.eth.estimate_gas(construct_tx)
        construct_tx["gas"] = int(gas_est * 1.25)
    except Exception:
        construct_tx["gas"] = 800_000

    print("Signing deployment transaction locally...")
    signed_tx = account.sign_transaction(construct_tx)

    print("Broadcasting raw transaction to Polygon Amoy...")
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = "0x" + tx_hash.hex()
    print(f"Transaction broadcast: {tx_hash_hex}")
    print("Waiting for transaction receipt...")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.get("status") != 1:
        sys.stderr.write(f"\n[FATAL] Deployment transaction reverted on-chain (tx: {tx_hash_hex}).\n")
        sys.exit(1)

    contract_address = receipt.get("contractAddress")
    block_number = receipt.get("blockNumber")

    # Post-deployment validation
    print("Validating deployed contract on-chain...")
    deployed_bytecode = w3.eth.get_code(contract_address)
    if not deployed_bytecode or len(deployed_bytecode) < 10:
        sys.stderr.write(f"\n[FATAL] No bytecode found at deployed address {contract_address}.\n")
        sys.exit(1)

    instance = w3.eth.contract(address=contract_address, abi=abi)
    owner = instance.functions.owner().call()
    is_relayer = instance.functions.authorizedRelayers(deployer_address).call()

    if owner.lower() != deployer_address.lower():
        sys.stderr.write(f"\n[WARNING] Contract owner mismatch: {owner} vs {deployer_address}\n")
    if not is_relayer:
        sys.stderr.write(f"\n[WARNING] Deployer is not registered as authorized relayer!\n")

    # Test dummy call
    dummy_anchor = instance.functions.getAuditAnchor(b"\x11" * 32).call()
    assert dummy_anchor[0] == b"\x00" * 32, "Initial anchor must be empty"
    is_valid = instance.functions.verifyAuditEvent(b"\x11" * 32, b"\x22" * 32).call()
    assert is_valid is False, "Initial verification must be False"

    print("\n" + "=" * 65)
    print(" ARGUS AUDIT ANCHOR — DEPLOYMENT SUCCESSFUL")
    print("=" * 65)
    print(f"Network:                     Polygon Amoy Testnet")
    print(f"Chain ID:                    {actual_chain_id}")
    print(f"Deployer Address:            {deployer_address}")
    print(f"Contract Address:            {contract_address}")
    print(f"Deployment Transaction Hash: {tx_hash_hex}")
    print(f"Block Number:                {block_number}")
    print(f"PolygonScan Explorer:        https://amoy.polygonscan.com/address/{contract_address}")
    print("-" * 65)
    print("NEXT STEP: Add the following to your environment / Render secrets:")
    print(f"BLOCKCHAIN_ENABLED=true")
    print(f"BLOCKCHAIN_RPC_URL={rpc_url}")
    print(f"BLOCKCHAIN_CHAIN_ID={actual_chain_id}")
    print(f"BLOCKCHAIN_CONTRACT_ADDRESS={contract_address}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
