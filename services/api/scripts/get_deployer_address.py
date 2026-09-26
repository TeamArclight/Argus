"""Helper script to inspect the ARGUS deployer/relayer wallet on Polygon Amoy.

Safely derives the public address from BLOCKCHAIN_PRIVATE_KEY and checks POL balance.
NEVER prints or exposes the private key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from web3 import Web3

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings


def main():
    settings = get_settings()

    priv_key = os.getenv("BLOCKCHAIN_PRIVATE_KEY") or settings.BLOCKCHAIN_PRIVATE_KEY
    if not priv_key or not priv_key.strip():
        sys.stderr.write(
            "\n[ERROR] BLOCKCHAIN_PRIVATE_KEY is not set.\n"
            "Please export BLOCKCHAIN_PRIVATE_KEY in your environment before running this script.\n"
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

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))

    try:
        if not w3.is_connected():
            sys.stderr.write(f"\n[ERROR] Unable to connect to RPC endpoint: {rpc_url}\n")
            sys.exit(1)
        actual_chain_id = w3.eth.chain_id
        if actual_chain_id != expected_chain_id:
            sys.stderr.write(
                f"\n[ERROR] Chain ID mismatch: RPC returned {actual_chain_id}, expected {expected_chain_id} (Polygon Amoy).\n"
            )
            sys.exit(1)
    except Exception as exc:
        sys.stderr.write(f"\n[ERROR] RPC query failed: {exc}\n")
        sys.exit(1)

    try:
        account = w3.eth.account.from_key(clean_key)
        deployer_address = account.address
    except Exception as exc:
        sys.stderr.write(f"\n[ERROR] Invalid private key: {exc}\n")
        sys.exit(1)

    balance_wei = w3.eth.get_balance(deployer_address)
    balance_pol = w3.from_wei(balance_wei, "ether")

    print("\n" + "=" * 65)
    print(" ARGUS POLYGON AMOY WALLET STATUS")
    print("=" * 65)
    print(f"Network:           Polygon Amoy Testnet")
    print(f"Chain ID:          {actual_chain_id}")
    print(f"RPC URL:           {rpc_url}")
    print(f"Wallet Address:    {deployer_address}")
    print(f"Current Balance:   {balance_pol:.4f} POL ({balance_wei} wei)")
    print(f"PolygonScan Link:  https://amoy.polygonscan.com/address/{deployer_address}")
    print("-" * 65)

    if balance_wei < w3.to_wei("0.02", "ether"):
        print("[STATUS] [WARN] INSUFFICIENT POL FOR CONTRACT DEPLOYMENT")
        print("Please fund this wallet with test POL before attempting deployment:")
        print("1. Visit the Polygon Faucet: https://faucet.polygon.technology/")
        print("2. Select Network: 'Polygon PoS (Amoy)'")
        print(f"3. Paste your Wallet Address: {deployer_address}")
        print("4. Request test POL and re-run this script to confirm arrival.")
    else:
        print("[STATUS] [OK] WALLET READY WITH SUFFICIENT POL FOR DEPLOYMENT")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
