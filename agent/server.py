"""Halong Yield's public surface: A2A agent card + JSON-RPC, ERC-8004 registration file,
deliverables, and the background loop (jobs, settlement, HelloFugu, treasury cycle).

Run locally:  uvicorn agent.server:app --port 8000
"""

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime

from bnbagent.erc8004.agent_uri import AgentURIGenerator
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import fugu, report, settings, state, strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("halong.server")

provider = None  # set at startup when AGENT_PRIVATE_KEY is configured

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
                        "a free preview: today's top Venus stablecoin APYs and what a hire would return."),
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
            {"name": "web", "endpoint": f"{settings.BASE_URL}/"},
        ],
        image=f"{settings.BASE_URL}/logo.svg",
        agent_id=settings.AGENT_ID,
        identity_registry=settings.IDENTITY_REGISTRY,
        chain_id=settings.CHAIN_ID,
        supported_trust=["reputation"],
    )
    return reg | {"x402Support": False, "active": True, "category": settings.CATEGORY,
                  "categories": [settings.CATEGORY], "tags": ["yield", "stablecoin", "venus", "bnb-chain", "erc-8183"]}


# ----------------------------------------------------------------- background


async def background_loop() -> None:
    while True:
        try:
            if provider is not None:
                await provider.tick()
                await asyncio.to_thread(fugu.tick)
                if time.time() - state.get("last_cycle_at") >= settings.CYCLE_HOURS * 3600:
                    await asyncio.to_thread(strategy.run_cycle)
        except Exception:
            log.exception("background tick failed")
        await asyncio.sleep(settings.TICK_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global provider
    if settings.AGENT_PRIVATE_KEY:
        from .provider import Provider
        provider = Provider()
        log.info("agent wallet %s, agent id %s, base %s", provider.address, settings.AGENT_ID, settings.BASE_URL)
    else:
        log.warning("AGENT_PRIVATE_KEY not set: serving read-only (no quotes, no deliveries)")
    task = asyncio.create_task(background_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.AGENT_NAME, lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "agent": settings.AGENT_NAME, "agent_id": settings.AGENT_ID,
            "wallet": provider.address if provider else None, "time": int(time.time())}


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
async def index():
    snapshot = await asyncio.to_thread(strategy.treasury_snapshot) if provider else None
    return {
        "name": settings.AGENT_NAME, "category": settings.CATEGORY, "description": settings.DESCRIPTION,
        "erc8004": {"chain_id": settings.CHAIN_ID, "registry": settings.IDENTITY_REGISTRY, "agent_id": settings.AGENT_ID},
        "agent_card": f"{settings.BASE_URL}/.well-known/agent-card.json",
        "price": f"{settings.JOB_PRICE_WEI / 1e18:g} U per assessment (ERC-8183, chain 97)",
        "treasury": snapshot,
        "jobs_delivered": len(state.get("submitted_jobs")),
    }


@app.get("/erc8183/job/{job_id}/response")
async def deliverable(job_id: int):
    path = settings.DATA_DIR / "deliverables" / f"erc8183-job-{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "no deliverable for this job")
    # Serve the stored bytes unchanged: the on-chain deliverable hash is computed over them.
    return FileResponse(path, media_type="application/json")


@app.get("/hellofugu/{sub_id}")
async def hellofugu_report(sub_id: int):
    info = state.get("fugu_reports").get(str(sub_id))
    if not info:
        raise HTTPException(404, "not a subscription to this agent")
    return info


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

    if skill in ("negotiate", "negotiate-erc8183-job"):
        if provider is None:
            return _rpc_error(req_id, -32000, "Quoting is not configured on this deployment")
        if data.get("chain_id") not in (None, settings.CHAIN_ID):
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
        if data.get("chain_id") not in (None, settings.CHAIN_ID):
            reply = {"status": "rejected", "job_id": job_id, "reason": f"escrow must be on chain {settings.CHAIN_ID}"}
        else:
            check = await provider.ops.verify_job(job_id)
            if check.get("valid"):
                asyncio.create_task(provider.deliver(job_id))
                reply = {"status": "accepted", "job_id": job_id}
            else:
                reply = {"status": "rejected", "job_id": job_id, "reason": check.get("error")}
        return {"jsonrpc": "2.0", "id": req_id, "result": _message(reply)}

    if skill == "yield_scan":
        task = data.get("task_description") or data.get("task") or text
        preview = await asyncio.to_thread(report.preview, task)
        top = ", ".join(f"{m['asset']} {m['supply_apy']:.2f}%" for m in preview["top_markets"])
        summary = (f"Top Venus stablecoin supply APYs on BNB Chain right now: {top}. "
                   f"Hire for the full assessment ({preview['price']}).")
        if not preview["can_answer"]:
            summary = preview["hint"]
        return {"jsonrpc": "2.0", "id": req_id, "result": _completed_task(preview, summary, message.get("contextId"))}

    return _rpc_error(req_id, -32602, f"Unknown skill: {skill!r}")
