"""ERC-8183 jobs this wallet created on BNB's AgenticCommerce kernel (BSC testnet).

Pokter and the other escrow marketplaces share this kernel; Pokter's public receipts index
says which of these jobs it brokered and for which agent.
"""

import json
from importlib.resources import files

import requests
from web3 import Web3

COMMERCE = Web3.to_checksum_address("0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE")
CAMPAIGN_START = 1790812800  # 2026-10-01 00:00 UTC
STATUSES = ["OPEN", "FUNDED", "SUBMITTED", "COMPLETED", "REJECTED", "EXPIRED"]
FUNDED_STATUSES = {"FUNDED", "SUBMITTED", "COMPLETED"}
POKTER_RECEIPTS = "https://pokter.xyz/api/v1/receipts"
LOG_STEP = 50_000


def _commerce(w3: Web3):
    abi = json.loads(files("bnbagent").joinpath("abis/AgenticCommerce.json").read_text(encoding="utf-8"))
    return w3.eth.contract(COMMERCE, abi=abi)


def _first_block_after(w3: Web3, timestamp: int) -> int:
    lo, hi = 0, w3.eth.block_number
    lo = max(0, hi - 6_000_000)  # BSC testnet makes well under 6M blocks in a campaign
    while lo < hi:
        mid = (lo + hi) // 2
        if w3.eth.get_block(mid)["timestamp"] < timestamp:
            lo = mid + 1
        else:
            hi = mid
    return lo


def jobs_by_client(w3: Web3, wallet: str) -> list[dict]:
    commerce = _commerce(w3)
    topic0 = Web3.keccak(text="JobCreated(uint256,address,address,address,uint256,address)").to_0x_hex()
    client_topic = "0x" + wallet[2:].lower().rjust(64, "0")
    start, latest = _first_block_after(w3, CAMPAIGN_START), w3.eth.block_number
    logs, block, step = [], start, LOG_STEP
    while block <= latest:
        end = min(block + step - 1, latest)
        try:
            logs += w3.eth.get_logs({"address": COMMERCE, "fromBlock": block, "toBlock": end,
                                     "topics": [topic0, None, client_topic]})
        except Exception:
            if step <= 5_000:
                raise
            step //= 2
            continue
        block = end + 1
    jobs = []
    for log in logs:
        job_id = commerce.events.JobCreated().process_log(log)["args"]["jobId"]
        job = commerce.functions.getJob(job_id).call()
        jobs.append({"jobId": job_id, "provider": job[2], "budget": job[5], "status": STATUSES[job[7]]})
    return jobs


def pokter_agents() -> dict[int, dict]:
    """jobId -> {tokenId, name} for every testnet job Pokter brokered."""
    r = requests.get(POKTER_RECEIPTS, params={"chain": 97, "limit": 500}, timeout=30)
    r.raise_for_status()
    return {int(x["jobId"]): x["agent"] for x in r.json().get("receipts", []) if x.get("chainId") == 97}
