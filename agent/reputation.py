"""ERC-8004 reputation: what hirers have said about the agent on chain.

Hirers rate through /rate (agent/web/rate.html), which sends giveFeedback from their own wallet;
the registry refuses feedback from the owner or the agent's operators, so every rating comes from
someone else.
"""

import time

from web3 import Web3

from . import chain, settings

REGISTRY = Web3.to_checksum_address("0x8004B663056A597Dffe9eCcC1965A193B7388713")  # BSC testnet
ABI = [
    {"type": "function", "name": "getClients", "stateMutability": "view",
     "inputs": [{"name": "agentId", "type": "uint256"}], "outputs": [{"type": "address[]"}]},
    {"type": "function", "name": "getSummary", "stateMutability": "view",
     "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "clientAddresses", "type": "address[]"},
                {"name": "tag1", "type": "string"}, {"name": "tag2", "type": "string"}],
     "outputs": [{"name": "count", "type": "uint64"}, {"name": "summaryValue", "type": "int128"},
                 {"name": "summaryValueDecimals", "type": "uint8"}]},
]
CACHE_SECONDS = 300
_cache: tuple[float, dict | None] = (0.0, None)


def summary() -> dict:
    """{count, average (0-100 scale), raters} across every rating left for this agent."""
    global _cache
    fetched_at, cached = _cache
    if cached is not None and time.time() - fetched_at < CACHE_SECONDS:
        return cached
    reg = chain.testnet().eth.contract(REGISTRY, abi=ABI)
    clients = reg.functions.getClients(settings.AGENT_ID).call() if settings.AGENT_ID else []
    result = {"count": 0, "average": None, "raters": len(clients), "registry": REGISTRY}
    if clients:
        count, value, decimals = reg.functions.getSummary(settings.AGENT_ID, clients, "", "").call()
        result |= {"count": count, "average": round(value / 10**decimals, 2) if count else None}
    _cache = (time.time(), result)
    return result


def rate_link(job_id: int | None = None) -> str:
    return f"{settings.BASE_URL}/rate" + (f"?job={job_id}" if job_id else "")
