"""Yield assessments: the deliverable of a paid job, and the free preview.

Everything is read live from Venus Core Pool on BNB Chain mainnet; nothing is signed and no
hirer funds are touched. The report says which block it was read at so it can be checked.
"""

import re
import time

from web3 import Web3

from . import chain, settings, strategy, venus

SCOPE_WORDS = ("yield", "apy", "apr", "interest", "earn", "lend", "lending", "supply", "deposit", "venus",
               "stable", "usdt", "usdc", "fdusd", "dai", "usd1", "usde", "busd", "savings", "park", "idle")
ASSETS = ("USDT", "USDC", "FDUSD", "USD1", "DAI", "USDe", "BUSD", "TUSD", "lisUSD")
DEFAULT_AMOUNT = 1_000.0
MAX_UTILIZATION = 92.0
SWAP_COST_PCT = 0.05  # assumed round-trip cost of swapping between dollar stablecoins
SCOPE_HINT = ("Halong Yield answers stablecoin yield questions on BNB Chain (Venus Core Pool). "
              "Ask for example: 'Where should I deposit 5,000 USDT for the best yield over 90 days?'")


def in_scope(task: str) -> bool:
    text = task.lower()
    return any(w in text for w in SCOPE_WORDS) or "$u" in text


def parse_brief(task: str) -> dict:
    asset = None
    for sym in ASSETS:
        if re.search(rf"\b{re.escape(sym)}\b", task, re.IGNORECASE):
            asset = next(a for a in ASSETS if a.lower() == sym.lower())
            break
    if asset is None and re.search(r"\$U\b", task):
        asset = "U"
    amount = DEFAULT_AMOUNT
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?\b", task.replace("$", ""), re.IGNORECASE)
    if m:
        value = float(m.group(1).replace(",", ""))
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((m.group(2) or "").lower(), 1)
        if value * mult >= 1:
            amount = value * mult
    days = 365
    h = re.search(r"(\d+)\s*(day|week|month|year)s?", task, re.IGNORECASE)
    if h:
        days = int(h.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[h.group(2).lower()]
    wallet = re.search(r"0x[a-fA-F0-9]{40}", task)
    return {"asset": asset, "amount": amount, "horizon_days": days,
            "wallet": Web3.to_checksum_address(wallet.group(0)) if wallet else None}


def _flags(m: venus.Market, amount: float) -> list[str]:
    flags = []
    if m.supply_apy <= 0:
        flags.append("pays nothing: no borrow demand")
    if m.utilization > MAX_UTILIZATION:
        flags.append(f"utilization {m.utilization:.1f}%: withdrawals may have to wait for repayments")
    if m.cash < amount * 5:
        flags.append(f"thin exit liquidity: {m.cash:,.0f} {m.asset} withdrawable")
    if m.asset not in ("USDT", "USDC"):
        flags.append("smaller or newer stablecoin: weigh issuer and depeg risk")
    return flags


def _wallet_positions(wallet: str, markets: list[venus.Market]) -> list[dict]:
    w3 = chain.mainnet()
    out = []
    for m in markets:
        v = w3.eth.contract(m.vtoken, abi=venus.VTOKEN_ABI)
        raw = v.functions.balanceOf(wallet).call() * v.functions.exchangeRateStored().call() // 10**18
        if raw:
            out.append({"asset": m.asset, "supplied": round(raw / 10**m.decimals, 2),
                        "supply_apy": round(m.supply_apy, 3)})
    return out


def assessment(task: str) -> dict:
    brief = parse_brief(task)
    amount, days = brief["amount"], brief["horizon_days"]
    block, markets = venus.mainnet_stable_markets()
    rows = [{**m.as_dict(), "flags": _flags(m, amount),
             "expected_earnings": round(amount * m.supply_apy / 100 * days / 365, 2)} for m in markets]
    clean = [r for r in rows if not any(f.startswith(("pays nothing", "utilization", "thin")) for f in r["flags"])]
    best = clean[0] if clean else rows[0]
    asked = next((r for r in rows if brief["asset"] and r["asset"] == brief["asset"]), None)

    if asked and asked is not best:
        spread = best["supply_apy"] - asked["supply_apy"]
        if spread > SWAP_COST_PCT * 365 / days:
            answer = (f"Supplying {amount:,.0f} {asked['asset']} to Venus earns {asked['supply_apy']:.2f}% APY "
                      f"(~{asked['expected_earnings']:,.2f} over {days} days). Swapping into {best['asset']} and "
                      f"supplying it earns {best['supply_apy']:.2f}% (~{best['expected_earnings']:,.2f}), "
                      f"{spread:.2f} pp more, which covers an assumed {SWAP_COST_PCT}% swap cost over this horizon.")
            pick = best
        else:
            answer = (f"Keep it in {asked['asset']}: Venus pays {asked['supply_apy']:.2f}% APY "
                      f"(~{asked['expected_earnings']:,.2f} over {days} days). {best['asset']} pays "
                      f"{spread:.2f} pp more, too little to cover swap costs over {days} days.")
            pick = asked
    else:
        pick = asked or best
        answer = (f"Supply {amount:,.0f} {pick['asset']} to Venus v{pick['asset']}: {pick['supply_apy']:.2f}% APY, "
                  f"about {pick['expected_earnings']:,.2f} {pick['asset']} over {days} days at today's rate.")

    report = {
        "agent": {"name": settings.AGENT_NAME, "category": settings.CATEGORY,
                  "erc8004": f"eip155:{settings.CHAIN_ID}:{settings.IDENTITY_REGISTRY}/{settings.AGENT_ID}"},
        "brief": {"task": task, **brief},
        "answer": answer,
        "recommendation": {"protocol": "Venus Core Pool (BNB Chain)", "market": pick["vtoken"],
                           "asset": pick["asset"], "supply_apy": pick["supply_apy"],
                           "expected_earnings": pick["expected_earnings"], "flags": pick["flags"]},
        "markets": rows,
        "risks": [
            "Supply APY is variable: it moves with borrow demand every block.",
            "Smart-contract and oracle risk of Venus Protocol.",
            "Stablecoin issuer and depeg risk, higher for smaller stablecoins.",
            "High utilization can delay withdrawals until borrowers repay.",
        ],
        "assumptions": [
            "APY = daily compounding of supplyRatePerBlock, the Venus UI convention.",
            f"Blocks per year measured from recent BNB Chain blocks ({markets[0].blocks_per_year:,}).",
            "1 stablecoin = 1 USD for liquidity figures.",
            f"Expected earnings hold today's rate constant for {days} days.",
            f"Swapping between stablecoins assumed to cost {SWAP_COST_PCT}%.",
        ],
        "data_sources": {"chain_id": 56, "block": block, "comptroller": venus.MAINNET_COMPTROLLER,
                         "rpc": settings.MAINNET_RPC, "read_at": int(time.time())},
    }
    if brief["wallet"]:
        report["wallet_positions"] = _wallet_positions(brief["wallet"], markets)
    try:
        report["agent_treasury"] = strategy.treasury_snapshot()
    except Exception:
        pass
    return report


def preview(task: str = "") -> dict:
    block, markets = venus.mainnet_stable_markets()
    return {
        "can_answer": in_scope(task) if task else True,
        "hint": None if (not task or in_scope(task)) else SCOPE_HINT,
        "top_markets": [{"asset": m.asset, "supply_apy": round(m.supply_apy, 3),
                         "utilization": round(m.utilization, 1)} for m in markets[:3]],
        "block": block,
        "a_hire_returns": ("A JSON assessment for your amount and horizon: every Venus stablecoin market ranked "
                           "by supply APY with liquidity and risk flags, expected earnings, whether a swap pays "
                           "for itself, your wallet's current positions if you give an address, and data sources."),
        "price": f"{settings.JOB_PRICE_WEI / 1e18:g} U per assessment, ERC-8183 escrow on BSC testnet (chain 97)",
    }
