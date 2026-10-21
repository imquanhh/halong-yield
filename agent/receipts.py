"""Signed decision receipts: why the agent moved its treasury, in a form anyone can verify.

A receipt is canonical JSON (sorted keys, no whitespace). Its proof is an EIP-191 signature by
the agent wallet over keccak256 of that JSON without the proof field, so

    Account.recover_message(encode_defunct(hexstr=proof.hash), signature=proof.signature)

must return the agent wallet registered on ERC-8004. Receipts are pinned to IPFS when a Pinata
key is configured, and indexed in receipts.json on the repo's `data` branch by GitHub Actions.
"""

import asyncio
import json
import time
from pathlib import Path

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from . import chain, settings

INDEX_URL = "https://raw.githubusercontent.com/imquanhh/halong-yield/data/receipts.json"
MAX_INDEXED = 200
SCHEME = "eip191-keccak256-canonical-json"
# Fields the index and the API add next to a receipt; they are not part of what was signed.
UNSIGNED_FIELDS = {"proof", "ipfs", "verified"}


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(body: dict) -> dict:
    digest = Web3.keccak(canonical(body))
    signed = Account.sign_message(encode_defunct(hexstr=digest.to_0x_hex()), chain.agent_account().key)
    return {**body, "proof": {"scheme": SCHEME, "hash": digest.to_0x_hex(),
                              "signature": Web3.to_hex(signed.signature), "signer": chain.agent_account().address}}


def verify(receipt: dict) -> bool:
    body = {k: v for k, v in receipt.items() if k not in UNSIGNED_FIELDS}
    proof = receipt.get("proof", {})
    digest = Web3.keccak(canonical(body)).to_0x_hex()
    if digest != proof.get("hash"):
        return False
    signer = Account.recover_message(encode_defunct(hexstr=digest), signature=proof["signature"])
    return signer == proof.get("signer")


def pin(receipt: dict) -> str | None:
    """Pin to IPFS and return the gateway URL, or None without a Pinata key."""
    if not settings.STORAGE_API_KEY:
        return None
    from .provider import _storage
    name = f"halong-receipt-{receipt['created_at']}.json"
    return asyncio.run(_storage().upload(receipt, name))


def new(kind: str, **fields) -> dict:
    return {"type": "halong-yield/decision-receipt", "version": 1, "kind": kind,
            "agent": {"name": settings.AGENT_NAME, "erc8004": f"{settings.CHAIN_ID}:{settings.AGENT_ID}",
                      "wallet": chain.agent_account().address},
            "created_at": int(time.time()), **fields}


def append_index(path: Path, receipt: dict, url: str | None) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "receipts": []}
    data["receipts"] = (data["receipts"] + [{**receipt, "ipfs": url}])[-MAX_INDEXED:]
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


_cache: tuple[float, list[dict]] = (0.0, [])


def index() -> list[dict]:
    """Every indexed receipt, oldest first, cached for 10 minutes."""
    global _cache
    fetched_at, cached = _cache
    if time.time() - fetched_at > 600:
        try:
            r = requests.get(INDEX_URL, timeout=10)
            cached = r.json().get("receipts", []) if r.ok else cached
        except (requests.RequestException, ValueError):
            pass
        _cache = (time.time(), cached)
    return cached


def recent(limit: int = 10) -> list[dict]:
    return index()[-limit:][::-1]
