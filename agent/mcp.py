"""A stateless MCP server (Streamable HTTP, JSON responses) at POST /mcp.

Lets Claude, Cursor or any MCP client use the agent directly. Tools are free reads; the full
assessment stays a paid ERC-8183 job, which the hire_quote tool starts by returning a quote
signed by the agent wallet. tools/list answers without a prior initialize, as marketplace
probes call it first.
"""

import json

from . import markets, risk, settings

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

TOOLS = [
    {
        "name": "yield_board",
        "title": "Stablecoin yield board",
        "description": ("Every dollar stablecoin market on Venus and Aave V3 (BNB Chain mainnet), ranked by "
                        "risk-adjusted APY for a given position size, with grade and the reasons for each penalty."),
        "inputSchema": {"type": "object", "properties": {
            "amount_usd": {"type": "number", "description": "Position size the ranking is for. Default 10000."},
            "limit": {"type": "integer", "description": "How many markets to return. Default 8."}},
            "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "market_detail",
        "title": "One market in detail",
        "description": "Supply/borrow APY, utilization, withdrawable liquidity, oracle price, penalties and 7-day stats.",
        "inputSchema": {"type": "object", "properties": {
            "protocol": {"type": "string", "enum": ["Venus", "Aave V3"]},
            "asset": {"type": "string", "description": "Stablecoin symbol, for example USDC."}},
            "required": ["protocol", "asset"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "hire_quote",
        "title": "Quote a full assessment",
        "description": ("Get a quote signed by the agent wallet for a full assessment of your brief (amount, horizon, "
                        "risk tolerance, optional wallet for a plan with unsigned transactions). Fund it on ERC-8183 "
                        "(BSC testnet, AgenticCommerce) to receive the report."),
        "inputSchema": {"type": "object", "properties": {"brief": {"type": "string"}},
                        "required": ["brief"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
]


def _board(args: dict) -> dict:
    amount = float(args.get("amount_usd") or 10_000)
    block, ms = markets.stable_markets()
    rows = [{"protocol": m.protocol, "asset": m.asset, "supply_apy": round(m.supply_apy, 3),
             "risk_adjusted_apy": a["risk_adjusted_apy"], "grade": a["grade"], "excluded": a["excluded"],
             "utilization": round(m.utilization, 1), "withdrawable": round(m.liquidity, 0),
             "penalties": [p["reason"] for p in a["penalties"]]}
            for m, a in risk.rank(ms, amount)]
    return {"block": block, "amount_usd": amount, "markets": rows[: int(args.get("limit") or 8)]}


def _detail(args: dict) -> dict:
    block, ms = markets.stable_markets()
    for m, a in risk.rank(ms, 10_000):
        if m.protocol == args.get("protocol") and m.asset.lower() == str(args.get("asset", "")).lower():
            return {"block": block, **m.as_dict(), **{k: a[k] for k in ("risk_adjusted_apy", "grade", "excluded",
                                                                         "penalties", "history")}}
    raise ValueError(f"no {args.get('protocol')} market for {args.get('asset')}")


def _quote(args: dict) -> dict:
    from .server import get_provider
    provider = get_provider()
    if provider is None:
        raise ValueError("quoting is not configured on this deployment")
    envelope = provider.negotiate({"task_description": args["brief"]})
    return {"quote": envelope, "how_to_fund": (
        "createJob on AgenticCommerce 0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE (BSC testnet) with provider "
        f"{provider.address} and this envelope as description (bnbagent build_job_description), registerJob, "
        "setBudget, approve U, fund; then send A2A notify_funded or wait for the poll."),
        "a2a": f"{settings.BASE_URL}/a2a"}


HANDLERS = {"yield_board": _board, "market_detail": _detail, "hire_quote": _quote}


def _result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(body: dict) -> dict | None:
    """One JSON-RPC message in, its response out (None for notifications)."""
    method, req_id = body.get("method"), body.get("id")
    if req_id is None:  # notification, e.g. notifications/initialized
        return None
    if method == "initialize":
        asked = (body.get("params") or {}).get("protocolVersion")
        return _result(req_id, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "halong-yield", "title": settings.AGENT_NAME, "version": settings.VERSION},
            "instructions": "Free reads of BNB Chain stablecoin yields; hire_quote starts a paid full assessment.",
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        params = body.get("params") or {}
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return _error(req_id, -32602, f"unknown tool {params.get('name')!r}")
        try:
            data = handler(params.get("arguments") or {})
        except Exception as exc:
            return _result(req_id, {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True})
        return _result(req_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                                "structuredContent": data})
    return _error(req_id, -32601, f"method not found: {method}")
