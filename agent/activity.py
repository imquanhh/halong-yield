"""Track record read straight from the chain: every ERC-8183 job funded for this agent and every
HelloFugu subscription to its listing, with how many ended complete. Cached for ten minutes."""

import time

from bnbagent.erc8183 import ERC8183Client

from . import fugu, settings

FIRST_JOB_ID = 1560              # jobs are numbered in order; none before this was the agent's (registered 2026-10-20)
BATCH = 200
CACHE_SECONDS = 600
_cache: tuple[float, dict | None] = (0.0, None)


def _jobs(provider: str) -> list[dict]:
    """Every job on the kernel since FIRST_JOB_ID whose provider is this agent, read by multicall."""
    commerce = ERC8183Client(None, settings.NETWORK).commerce  # read-only
    ids = list(range(FIRST_JOB_ID, commerce.job_counter() + 1))
    out = []
    for i in range(0, len(ids), BATCH):
        for job in commerce.get_jobs_batch(ids[i:i + BATCH]):
            if job is not None and job.provider.lower() == provider.lower():
                out.append({"job_id": job.id, "client": job.client, "status": job.status.name,
                            "budget": job.budget / 1e18})
    return out


def track_record(agent_wallet: str) -> dict:
    global _cache
    fetched_at, cached = _cache
    if cached is not None and time.time() - fetched_at < CACHE_SECONDS:
        return cached
    jobs = [j for j in _jobs(agent_wallet) if j["status"] != "OPEN"]  # OPEN = created, never funded
    subs = []
    lid = fugu.listing_id()
    if lid:
        sub = fugu._subscription()
        count = sub.functions.subCount().call()
        for sub_id in range(max(1, count - fugu.SCAN_DEPTH + 1), count + 1):
            s = sub.functions.getSub(sub_id).call()
            if s[0] == lid:
                subs.append({"sub_id": sub_id, "client": s[1], "ended": time.time() >= s[6], "cancelled": s[7],
                             "complete": time.time() >= s[6] and s[4] == s[3]})
    hirers = {j["client"].lower() for j in jobs} | {s["client"].lower() for s in subs}
    record = {
        "hires": len(jobs) + len(subs),
        "distinct_hirers": len(hirers),
        "completed": sum(j["status"] == "COMPLETED" for j in jobs) + sum(s["complete"] for s in subs),
        "erc8183_jobs": {st: sum(j["status"] == st for j in jobs) for st in sorted({j["status"] for j in jobs})},
        "hellofugu_subscriptions": len(subs),
        "read_at": int(time.time()),
    }
    _cache = (time.time(), record)
    return record
