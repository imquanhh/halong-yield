"""HelloFugu: subscriptions to this agent's listing.

A HelloFugu hire is FuguSubscription.subscribe: the renter's tBNB is escrowed and streamed to
the listing owner by the second. While a subscription runs, /hellofugu/<subId> serves a live
yield report for the renter's wallet. Once it ends, the agent calls the permissionless claim(),
which pays out the rest and marks the hire complete (claimed == deposited).
"""

import logging
import time

from web3 import Web3

from . import chain, report, settings

log = logging.getLogger("halong.fugu")

REGISTRY = Web3.to_checksum_address("0xb2f36070E6eae3353E8e755172B477DF213ae248")
SUBSCRIPTION = Web3.to_checksum_address("0xfdb083371f44Cf53181350389D3217e51B431776")
SCAN_DEPTH = 300  # most recent subscriptions checked on each poll

REGISTRY_ABI = [{"type": "function", "name": "listingByAgentId", "stateMutability": "view",
                 "inputs": [{"name": "", "type": "uint256"}], "outputs": [{"type": "uint256"}]}]
SUB_TUPLE = {"type": "tuple", "components": [
    {"name": "listingId", "type": "uint256"}, {"name": "subscriber", "type": "address"},
    {"name": "payToken", "type": "address"}, {"name": "deposited", "type": "uint128"},
    {"name": "claimed", "type": "uint128"}, {"name": "startedAt", "type": "uint64"},
    {"name": "endsAt", "type": "uint64"}, {"name": "cancelled", "type": "bool"}, {"name": "feeBps", "type": "uint16"}]}
SUBSCRIPTION_ABI = [
    {"type": "function", "name": "subCount", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "getSub", "stateMutability": "view",
     "inputs": [{"name": "subId", "type": "uint256"}], "outputs": [SUB_TUPLE]},
    {"type": "function", "name": "claimable", "stateMutability": "view",
     "inputs": [{"name": "subId", "type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "claim", "stateMutability": "nonpayable",
     "inputs": [{"name": "subId", "type": "uint256"}], "outputs": []},
]


def _subscription():
    return chain.testnet().eth.contract(SUBSCRIPTION, abi=SUBSCRIPTION_ABI)


def listing_id() -> int:
    if not settings.AGENT_ID:
        return 0
    reg = chain.testnet().eth.contract(REGISTRY, abi=REGISTRY_ABI)
    return reg.functions.listingByAgentId(settings.AGENT_ID).call()


def claim_ended() -> list[dict]:
    """Claim every ended subscription to our listing that still holds escrow."""
    lid = listing_id()
    if not lid:
        return []
    sub = _subscription()
    count = sub.functions.subCount().call()
    claimed = []
    for sub_id in range(max(1, count - SCAN_DEPTH + 1), count + 1):
        s = sub.functions.getSub(sub_id).call()
        if s[0] != lid or time.time() < s[6] or sub.functions.claimable(sub_id).call() == 0:
            continue
        tx = chain.send(sub.functions.claim(sub_id))
        log.info("claimed HelloFugu subscription %s: %s/tx/%s", sub_id, settings.EXPLORER, tx)
        claimed.append({"sub_id": sub_id, "tx": tx})
    return claimed


def subscription_report(sub_id: int) -> dict | None:
    s = _subscription().functions.getSub(sub_id).call()
    if not s[0] or s[0] != listing_id():
        return None
    return {
        "subscription": {"id": sub_id, "subscriber": s[1], "started_at": s[5], "ends_at": s[6],
                         "active": not s[7] and time.time() < s[6]},
        "report": report.assessment(f"Best stablecoin yield on Venus for wallet {s[1]}"),
    }
