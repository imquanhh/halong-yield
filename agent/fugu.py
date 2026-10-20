"""HelloFugu: subscriptions to this agent's listing.

A HelloFugu hire is FuguSubscription.subscribe: the renter's tBNB is escrowed and streamed to
the listing owner by the second. While a subscription runs, the agent keeps a yield report
for the renter's wallet at /hellofugu/<subId>. Once it ends, the agent calls the permissionless
claim(), which pays out the rest and marks the hire complete (claimed == deposited).
"""

import logging
import time

from web3 import Web3

from . import chain, report, settings, state

log = logging.getLogger("halong.fugu")

REGISTRY = Web3.to_checksum_address("0xb2f36070E6eae3353E8e755172B477DF213ae248")
SUBSCRIPTION = Web3.to_checksum_address("0xfdb083371f44Cf53181350389D3217e51B431776")
RESCAN_DEPTH = 200
REPORT_REFRESH_SECONDS = 3600

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


def listing_id() -> int:
    if not settings.AGENT_ID:
        return 0
    reg = chain.testnet().eth.contract(REGISTRY, abi=REGISTRY_ABI)
    return reg.functions.listingByAgentId(settings.AGENT_ID).call()


def tick() -> None:
    lid = listing_id()
    if not lid:
        return
    sub = chain.testnet().eth.contract(SUBSCRIPTION, abi=SUBSCRIPTION_ABI)
    count = sub.functions.subCount().call()
    cursor = state.get("fugu_sub_cursor") or max(0, count - RESCAN_DEPTH)
    for sub_id in range(cursor + 1, count + 1):
        s = sub.functions.getSub(sub_id).call()
        if s[0] == lid:
            state.update(lambda st: st["fugu_reports"].setdefault(
                str(sub_id), {"subscriber": s[1], "ends_at": s[6], "report": None, "updated": 0}))
    state.update(lambda st: st.__setitem__("fugu_sub_cursor", count))

    now = time.time()
    for key, info in list(state.get("fugu_reports").items()):
        if info.get("claimed"):
            continue
        sub_id = int(key)
        if now < info["ends_at"] and now - info["updated"] > REPORT_REFRESH_SECONDS:
            rep = report.assessment(f"Best stablecoin yield on Venus for wallet {info['subscriber']}")
            state.update(lambda st: st["fugu_reports"][key].update(report=rep, updated=int(now)))
        elif now >= info["ends_at"]:
            if sub.functions.claimable(sub_id).call() > 0:
                tx = chain.send(sub.functions.claim(sub_id))
                state.log_action("claim", f"claimed HelloFugu subscription {sub_id} after it ended", tx)
            state.update(lambda st: st["fugu_reports"][key].__setitem__("claimed", True))
