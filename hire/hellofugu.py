"""HelloFugu (BSC testnet only): a hire is FuguSubscription.subscribe, paid in native tBNB.

Mirrors the marketplace's own hire button (frontend/src/components/wallet/hire-action.tsx
in github.com/Lexirieru/fugugent): price read from FuguRegistry, maxAmount = fresh quote
+ 1%, deadline = latest block time + 10 minutes, simulate before signing.
The hire event is `Subscribed(subId, listingId, subscriber, payToken, amount)`.
"""

import math

import requests
from eth_account.signers.local import LocalAccount
from web3 import Web3

from .config import EXPLORER_TX, latest_timestamp, send_tx

SUBSCRIPTION = Web3.to_checksum_address("0xfdb083371f44Cf53181350389D3217e51B431776")
REGISTRY = Web3.to_checksum_address("0xb2f36070E6eae3353E8e755172B477DF213ae248")
ORACLE = Web3.to_checksum_address("0xB5f72a0ab0bA971c8C4F69D4A075cB7fd7859e65")
NATIVE = "0x0000000000000000000000000000000000000000"
TRACKING_API = "https://api.hellofugu.xyz/api/tracking"
DEADLINE_WINDOW = 600

CATEGORIES = ["rebalancing", "grid", "yield", "health factor", "hiring", "commerce", "autonomous", "streaming", "treasury"]

SUBSCRIPTION_ABI = [
    {"type": "function", "name": "subscribe", "stateMutability": "payable",
     "inputs": [{"name": "listingId", "type": "uint256"}, {"name": "periods", "type": "uint32"},
                {"name": "payToken", "type": "address"}, {"name": "maxAmount", "type": "uint256"},
                {"name": "deadline", "type": "uint256"}],
     "outputs": [{"name": "subId", "type": "uint256"}]},
    {"type": "function", "name": "hasSubscribed", "stateMutability": "view",
     "inputs": [{"name": "listingId", "type": "uint256"}, {"name": "user", "type": "address"}],
     "outputs": [{"type": "bool"}]},
    {"type": "event", "name": "Subscribed", "anonymous": False,
     "inputs": [{"name": "subId", "type": "uint256", "indexed": True},
                {"name": "listingId", "type": "uint256", "indexed": True},
                {"name": "subscriber", "type": "address", "indexed": True},
                {"name": "payToken", "type": "address", "indexed": False},
                {"name": "amount", "type": "uint256", "indexed": False}]},
]
REGISTRY_ABI = [
    {"type": "function", "name": "getListing", "stateMutability": "view",
     "inputs": [{"name": "listingId", "type": "uint256"}],
     "outputs": [{"type": "tuple", "components": [
         {"name": "erc8004AgentId", "type": "uint256"}, {"name": "owner", "type": "address"},
         {"name": "agentWallet", "type": "address"}, {"name": "category", "type": "uint8"},
         {"name": "priceUsd8PerPeriod", "type": "uint128"}, {"name": "periodSeconds", "type": "uint32"},
         {"name": "active", "type": "bool"}, {"name": "curated", "type": "bool"},
         {"name": "metadataURI", "type": "string"}]}]},
]
ORACLE_ABI = [
    {"type": "function", "name": "quote", "stateMutability": "view",
     "inputs": [{"name": "token", "type": "address"}, {"name": "usdAmount8", "type": "uint256"}],
     "outputs": [{"name": "tokenAmount", "type": "uint256"}]},
]


def _contracts(w3: Web3):
    return (w3.eth.contract(SUBSCRIPTION, abi=SUBSCRIPTION_ABI),
            w3.eth.contract(REGISTRY, abi=REGISTRY_ABI),
            w3.eth.contract(ORACLE, abi=ORACLE_ABI))


def prepare(w3: Web3, client: str, listing_id: int, duration_s: int) -> dict:
    """Read the listing and size the deposit. No signing, no state change."""
    sub, reg, oracle = _contracts(w3)
    agent_id, _owner, _wallet, category, price_usd8, period_s, active, _curated, _uri = \
        reg.functions.getListing(listing_id).call()
    if not active:
        raise RuntimeError(f"HelloFugu listing {listing_id} đang inactive.")
    periods = max(1, math.ceil(duration_s / period_s))
    amount = oracle.functions.quote(NATIVE, price_usd8 * periods).call()
    if amount == 0:
        raise RuntimeError("Oracle không trả giá tBNB, thử lại sau.")
    return {
        "marketplace": "HelloFugu",
        "listingId": listing_id,
        "agent": f"97:{agent_id}",
        "category": CATEGORIES[category] if category < len(CATEGORIES) else str(category),
        "periods": periods,
        "durationSeconds": periods * period_s,
        "usd": price_usd8 * periods / 1e8,
        "amountWei": amount,
        "alreadyHired": sub.functions.hasSubscribed(listing_id, client).call(),
    }


def hire(w3: Web3, acct: LocalAccount, plan: dict) -> dict:
    sub, _reg, oracle = _contracts(w3)
    # Re-quote right before signing, as the marketplace UI does.
    fresh = prepare(w3, acct.address, plan["listingId"], plan["durationSeconds"])
    amount = fresh["amountWei"]
    max_amount = amount + amount // 100
    deadline = latest_timestamp(w3) + DEADLINE_WINDOW
    fn = sub.functions.subscribe(plan["listingId"], fresh["periods"], NATIVE, max_amount, deadline)
    fn.call({"from": acct.address, "value": amount})  # simulate first
    receipt = send_tx(w3, acct, fn.build_transaction({"from": acct.address, "value": amount}))
    events = sub.events.Subscribed().process_receipt(receipt)
    if not events:
        raise RuntimeError("Giao dịch thành công nhưng không thấy event Subscribed.")
    tx = receipt["transactionHash"].to_0x_hex()
    return {**fresh, "subId": events[0]["args"]["subId"], "txHash": tx, "explorer": EXPLORER_TX + tx}


def tracked_hires(wallet: str) -> list:
    r = requests.get(f"{TRACKING_API}/hires", params={"wallet": wallet}, timeout=30)
    r.raise_for_status()
    return r.json().get("hires", [])
