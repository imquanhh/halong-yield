"""Campaign wallet, BSC testnet connection and the shared transaction helper."""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CHAIN_ID = 97
DEFAULT_RPC = "https://bsc-testnet-rpc.publicnode.com"
EXPLORER_TX = "https://testnet.bscscan.com/tx/"
FAUCET = "https://www.bnbchain.org/en/testnet-faucet"
HIRES_LOG = ROOT / "hires.json"


def connect() -> Web3:
    w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL") or DEFAULT_RPC, request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)  # BSC block headers carry POA extraData
    if w3.eth.chain_id != CHAIN_ID:
        sys.exit(f"RPC đang trỏ tới chain {w3.eth.chain_id}, không phải BSC testnet ({CHAIN_ID}).")
    return w3


def campaign_account() -> LocalAccount:
    key = (os.getenv("PRIVATE_KEY") or "").strip()
    if not key:
        sys.exit("PRIVATE_KEY trong .env đang trống. Điền private key ví campaign rồi chạy lại.")
    if not key.startswith("0x"):
        key = "0x" + key
    try:
        acct = Account.from_key(key)
    except Exception:
        sys.exit("PRIVATE_KEY không hợp lệ (cần 64 ký tự hex).")
    expected = (os.getenv("CAMPAIGN_WALLET") or "").strip()
    if expected and expected.lower() != acct.address.lower():
        sys.exit(f"PRIVATE_KEY ra ví {acct.address}, không khớp CAMPAIGN_WALLET={expected}.")
    return acct


def fill_tx(w3: Web3, sender: str, tx: dict) -> dict:
    """Legacy gas pricing, fresh nonce, 20% gas headroom."""
    tx = {k: v for k, v in tx.items() if k not in ("gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas")}
    tx |= {
        "from": sender,
        "chainId": CHAIN_ID,
        "nonce": w3.eth.get_transaction_count(sender, "pending"),
        "gasPrice": w3.eth.gas_price,
    }
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    return tx


def send_tx(w3: Web3, acct: LocalAccount, tx: dict) -> dict:
    """Sign, broadcast and wait. Raises if the tx reverts."""
    signed = acct.sign_transaction(fill_tx(w3, acct.address, tx))
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        raise RuntimeError(f"Giao dịch bị revert: {EXPLORER_TX}{tx_hash.to_0x_hex()}")
    return receipt


def latest_timestamp(w3: Web3) -> int:
    return int(w3.eth.get_block("latest")["timestamp"])


def log_hire(entry: dict) -> None:
    rows = json.loads(HIRES_LOG.read_text(encoding="utf-8")) if HIRES_LOG.exists() else []
    rows.append({**entry, "loggedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    HIRES_LOG.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
