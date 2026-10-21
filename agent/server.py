"""Halong Yield's public surface: the dashboard, A2A agent card + JSON-RPC, ERC-8004 registration
file, per-subscription HelloFugu reports. Stateless, so it runs as a Vercel function
(api/index.py); scheduled work lives in agent/worker.py.

Run locally:  uvicorn agent.server:app --port 8000
"""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from bnbagent.erc8004.agent_uri import AgentURIGenerator
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import activity, fugu, history, markets, mcp, receipts, report, reputation, risk, settings, strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("halong.server")

# notify_funded delivers inside the request; past this the 10-minute poll finishes the job.
DELIVER_TIMEOUT_SECONDS = 45
# /tick is public so any uptime pinger can drive delivery; one run per instance per window.
TICK_MIN_INTERVAL = 120
_last_tick = 0.0


@lru_cache(maxsize=1)
def get_provider():
    """The ERC-8183 seller, or None on a deployment without AGENT_PRIVATE_KEY (read-only)."""
    if not settings.AGENT_PRIVATE_KEY:
        return None
    from .provider import Provider
    return Provider()

SKILLS = [
    {
        "id": "negotiate",
        "name": "Quote a yield assessment",
        "description": ('Send {"skill": "negotiate", "task_description": "...", "terms": {...}} and receive a '
                        "quote signed by this agent's wallet for an ERC-8183 job on BSC testnet (chain 97), "
                        "priced in U. Briefs outside stablecoin yield are declined here, before any money moves."),
        "tags": ["erc8183", "negotiation", "quote"],
    },
    {
        "id": "notify_funded",
        "name": "Start a funded job",
        "description": ('After funding, send {"skill": "notify_funded", "job_id": <int>, "chain_id": 97}. The agent '
                        "re-reads the escrow, replies accepted or rejected, then builds the assessment and submits "
                        "it on chain. Funded jobs are also picked up by polling."),
        "tags": ["erc8183", "delivery"],
    },
    {
        "id": "yield_scan",
        "name": "Stablecoin yield scan (free preview)",
        "description": ("Ask where a stablecoin amount earns the most on BNB Chain. Calling this skill directly returns "
                        "a free preview: today's best risk-adjusted stablecoin markets on Venus and Aave V3 and what a "
                        "hire would return (full ranking, analyst note, and a wallet plan with unsigned transactions)."),
        "tags": ["yield", "stablecoin", "venus", "apy"],
        "examples": ["Where should I deposit 5,000 USDT for the best yield over 90 days?",
                     "Best stablecoin supply APY on Venus right now for 20k USDC?",
                     "Is it worth swapping my FDUSD into USDT for yield?"],
    },
]
for _s in SKILLS:
    _s |= {"inputModes": ["application/json", "text/plain"], "outputModes": ["application/json"]}


def agent_card() -> dict:
    return {
        "protocolVersion": "0.3.0",
        "name": settings.AGENT_NAME,
        "description": settings.DESCRIPTION,
        "url": f"{settings.BASE_URL}/a2a",
        "preferredTransport": "JSONRPC",
        "version": settings.VERSION,
        "provider": {"organization": settings.AGENT_NAME, "url": settings.BASE_URL},
        "documentationUrl": settings.REPO_URL,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": SKILLS,
    }


def registration_file() -> dict:
    reg = AgentURIGenerator.generate_registration_file(
        name=settings.AGENT_NAME,
        description=settings.DESCRIPTION,
        endpoints=[
            {"name": "A2A", "endpoint": f"{settings.BASE_URL}/.well-known/agent-card.json", "version": "0.3.0"},
            {"name": "MCP", "endpoint": f"{settings.BASE_URL}/mcp", "version": mcp.PROTOCOL_VERSION},
            {"name": "web", "endpoint": f"{settings.BASE_URL}/"},
            # The public repo whose README names registry ID and chain; marketplaces read it from here.
            {"name": "source", "endpoint": settings.REPO_URL},
        ],
        image=f"{settings.BASE_URL}/logo.svg",
        agent_id=settings.AGENT_ID,
        identity_registry=settings.IDENTITY_REGISTRY,
        chain_id=settings.CHAIN_ID,
        supported_trust=["reputation"],
    )
    return reg | {"x402Support": False, "active": True, "category": settings.CATEGORY,
                  "categories": [settings.CATEGORY], "tags": ["yield", "stablecoin", "venus", "aave", "risk-adjusted", "bnb-chain", "erc-8183"]}


app = FastAPI(title=settings.AGENT_NAME)


@app.get("/health")
async def health():
    provider = get_provider()
    return {"ok": True, "agent": settings.AGENT_NAME, "agent_id": settings.AGENT_ID,
            "wallet": provider.address if provider else None, "base_url": settings.BASE_URL,
            "deliverables": "ipfs" if settings.STORAGE_API_KEY else "local", "gateway": settings.STORAGE_GATEWAY_URL,
            "cron_configured": bool(settings.CRON_SECRET), "time": int(time.time())}


@app.get("/.well-known/agent-card.json")
async def get_agent_card():
    return agent_card()


@app.get("/.well-known/agent-registration.json")
async def get_registration():
    return registration_file()


@app.get("/logo.svg")
async def logo():
    return FileResponse(settings.ROOT / "agent" / "logo.svg", media_type="image/svg+xml")


@app.get("/")
async def dashboard():
    return FileResponse(settings.ROOT / "agent" / "web" / "index.html", media_type="text/html")


def _overview() -> dict:
    block, ms = markets.stable_markets()
    rows = [{**m.as_dict(), "risk_adjusted_apy": a["risk_adjusted_apy"], "grade": a["grade"],
             "excluded": a["excluded"], "penalties": a["penalties"], "history": history.series(m.key, 48)}
            for m, a in risk.rank(ms, 10_000)]
    treasury = weights = None
    if settings.AGENT_PRIVATE_KEY:
        treasury = strategy.treasury_snapshot()
        weights = strategy.weights(strategy.signal()[1])
    recent = [{**r, "verified": receipts.verify(r)} for r in receipts.recent(5)]
    return {"agent": {"name": settings.AGENT_NAME, "erc8004": f"{settings.CHAIN_ID}:{settings.AGENT_ID}"},
            "block": block, "markets": rows, "treasury": treasury, "weights": weights,
            "receipts": recent, "receipts_total": len(receipts.index()), "read_at": int(time.time())}


@app.get("/api/overview")
async def overview():
    return await asyncio.to_thread(_overview)


@app.get("/api/track")
async def track():
    def build():
        record = activity.track_record(get_provider().address) if settings.AGENT_PRIVATE_KEY else None
        return {"track_record": record, "reputation": reputation.summary(), "rate": reputation.rate_link()}
    return await asyncio.to_thread(build)


@app.get("/rate")
async def rate_page():
    return FileResponse(settings.ROOT / "agent" / "web" / "rate.html", media_type="text/html")


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    messages = body if isinstance(body, list) else [body]
    replies = [r for r in [await asyncio.to_thread(mcp.handle, m) for m in messages if isinstance(m, dict)] if r]
    if not replies:
        return JSONResponse(None, status_code=202)
    return JSONResponse(replies if isinstance(body, list) else replies[0])


@app.get("/mcp")
async def mcp_get():
    return JSONResponse({"error": "POST JSON-RPC to this endpoint (MCP Streamable HTTP, stateless)"}, 405,
                        headers={"Allow": "POST"})


@app.get("/tick")
async def public_tick():
    """Deliver funded jobs, settle, claim. Safe to call by anyone: it only does the agent's own due work."""
    global _last_tick
    if not settings.AGENT_PRIVATE_KEY:
        return {"skipped": "not configured"}
    if time.time() - _last_tick < TICK_MIN_INTERVAL:
        return {"skipped": f"ran less than {TICK_MIN_INTERVAL}s ago", "ok": True}
    _last_tick = time.time()
    from . import worker
    return {"ok": True, **await worker.tick()}


@app.get("/api/status")
async def status():
    snapshot = await asyncio.to_thread(strategy.treasury_snapshot) if settings.AGENT_PRIVATE_KEY else None
    return {
        "name": settings.AGENT_NAME, "category": settings.CATEGORY, "description": settings.DESCRIPTION,
        "erc8004": {"chain_id": settings.CHAIN_ID, "registry": settings.IDENTITY_REGISTRY, "agent_id": settings.AGENT_ID},
        "agent_card": f"{settings.BASE_URL}/.well-known/agent-card.json",
        "price": f"{settings.JOB_PRICE_WEI / 1e18:g} U per assessment (ERC-8183, chain 97)",
        "hellofugu_listing": await asyncio.to_thread(fugu.listing_id),
        "treasury": snapshot,
        "source": "https://github.com/imquanhh/halong-yield",
    }


@app.get("/erc8183/job/{job_id}/response")
async def deliverable(job_id: int):
    """Local development only: production deliverables are pinned to IPFS."""
    path = settings.DATA_DIR / "deliverables" / f"erc8183-job-{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "no deliverable for this job")
    # Serve the stored bytes unchanged: the on-chain deliverable hash is computed over them.
    return FileResponse(path, media_type="application/json")


@app.get("/cron/{task}")
async def cron(task: str, request: Request):
    """Daily Vercel Cron backup for the GitHub Actions schedule (see vercel.json)."""
    if not settings.CRON_SECRET or request.headers.get("authorization") != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(401, "unauthorized")
    from . import worker
    if task == "tick":
        return await worker.tick()
    if task == "cycle":
        return await asyncio.to_thread(strategy.run_cycle)
    raise HTTPException(404, "unknown task")


@app.get("/hellofugu/{sub_id}")
async def hellofugu_report(sub_id: int):
    result = await asyncio.to_thread(fugu.subscription_report, sub_id)
    if result is None:
        raise HTTPException(404, "not a subscription to this agent")
    return result


# ----------------------------------------------------------------------- A2A


def _rpc_error(req_id, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
                        status_code=status)


def _message(data: dict | None, text: str | None = None) -> dict:
    parts = ([{"kind": "text", "text": text}] if text else []) + ([{"kind": "data", "data": data}] if data else [])
    return {"kind": "message", "role": "agent", "messageId": str(uuid.uuid4()), "parts": parts}


def _completed_task(data: dict, text: str, context_id: str | None) -> dict:
    return {
        "kind": "task", "id": str(uuid.uuid4()), "contextId": context_id or str(uuid.uuid4()),
        "status": {"state": "completed", "timestamp": datetime.now(UTC).isoformat(),
                   "message": _message(None, text)},
        "artifacts": [{"artifactId": str(uuid.uuid4()), "name": "yield-scan",
                       "parts": [{"kind": "text", "text": text}, {"kind": "data", "data": data}]}],
    }


def _sells_on_our_chain(data: dict) -> bool:
    """True if the request names no chain, or names ours (as int or string, in chain_id or escrow_chains)."""
    named = {data.get("chain_id"), *(data.get("escrow_chains") or [])} - {None}
    try:
        return not named or settings.CHAIN_ID in {int(c) for c in named}
    except (TypeError, ValueError):
        return False


def _parse(message: dict) -> tuple[dict, str]:
    data, texts = {}, []
    for part in message.get("parts", []):
        if part.get("kind") == "data" and isinstance(part.get("data"), dict):
            data = part["data"]
        elif part.get("kind") == "text":
            texts.append(part.get("text", ""))
    return data, " ".join(texts).strip()


@app.post("/a2a")
async def a2a(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error", 400)
    req_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
        return _rpc_error(req_id, -32601, f"Method not supported: {body.get('method')}")
    message = (body.get("params") or {}).get("message") or {}
    data, text = _parse(message)
    skill = data.get("skill") or "yield_scan"
    provider = get_provider() if skill in ("negotiate", "negotiate-erc8183-job", "notify_funded") else None

    if skill in ("negotiate", "negotiate-erc8183-job"):
        if provider is None:
            return _rpc_error(req_id, -32000, "Quoting is not configured on this deployment")
        if not _sells_on_our_chain(data):
            return {"jsonrpc": "2.0", "id": req_id, "result": _message({"response": {
                "accepted": False, "reason": f"This agent sells on chain {settings.CHAIN_ID} (BSC testnet) only."}})}
        envelope = await asyncio.to_thread(provider.negotiate, data)
        return {"jsonrpc": "2.0", "id": req_id, "result": _message(envelope)}

    if skill == "notify_funded":
        if provider is None:
            return _rpc_error(req_id, -32000, "Delivery is not configured on this deployment")
        try:
            job_id = int(data.get("job_id"))
        except (TypeError, ValueError):
            return _rpc_error(req_id, -32602, "notify_funded needs an integer job_id")
        if not _sells_on_our_chain({"chain_id": data.get("chain_id")}):
            reply = {"status": "rejected", "job_id": job_id, "reason": f"escrow must be on chain {settings.CHAIN_ID}"}
        else:
            check = await provider.ops.verify_job(job_id)
            if not check.get("valid"):
                reply = {"status": "rejected", "job_id": job_id, "reason": check.get("error")}
            else:
                # A serverless function cannot keep working after it answers, so deliver first.
                reply = {"status": "accepted", "job_id": job_id}
                try:
                    done = await asyncio.wait_for(provider.deliver(job_id), DELIVER_TIMEOUT_SECONDS)
                    if done.get("status") == "submitted":
                        reply |= {"submitted": True, "tx": done["tx"], "deliverable_url": done["deliverable_url"]}
                except Exception:
                    log.exception("in-request delivery of job %s failed; the scheduled poll will retry", job_id)
        return {"jsonrpc": "2.0", "id": req_id, "result": _message(reply)}

    if skill == "yield_scan":
        task = data.get("task_description") or data.get("task") or text
        preview = await asyncio.to_thread(report.preview, task)
        top = ", ".join(f"{m['protocol']} {m['asset']} {m['supply_apy']:.2f}% ({m['risk_adjusted_apy']:.2f}% risk-adjusted)"
                        for m in preview["top_markets"])
        summary = (f"Best risk-adjusted stablecoin markets on BNB Chain right now, out of {preview['markets_compared']}: "
                   f"{top}. Hire for the full assessment ({preview['price']}).")
        if not preview["can_answer"]:
            summary = preview["hint"]
        return {"jsonrpc": "2.0", "id": req_id, "result": _completed_task(preview, summary, message.get("contextId"))}

    return _rpc_error(req_id, -32602, f"Unknown skill: {skill!r}")
