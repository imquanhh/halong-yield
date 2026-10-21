"""Yield assessments: the deliverable of a paid job, and the free preview.

Everything is read live from BNB Chain mainnet (Venus Core Pool and Aave V3); nothing is signed
and no hirer funds are touched. Markets are ranked on risk-adjusted APY (agent/risk.py), filtered
by the hirer's risk tolerance, and, when the brief names a wallet, turned into a step-by-step
plan with unsigned transactions (agent/wallet.py). Paid jobs add an analyst note written by
Claude from the computed facts; without an API key a template note says the same.
"""

import re
import time

from web3 import Web3

from . import aave, llm, markets, risk, settings, strategy, venus, wallet

SCOPE_WORDS = ("yield", "apy", "apr", "interest", "earn", "lend", "lending", "supply", "deposit", "venus", "aave",
               "stable", "usdt", "usdc", "fdusd", "dai", "usd1", "usde", "busd", "savings", "park", "idle", "optimi")
# Briefs that belong to another kind of agent. Anything else (including a generic or empty
# brief, as marketplace probes send) is quoted as this agent's one service: a yield assessment.
OFF_TOPIC_WORDS = ("safe to buy", "token safety", "honeypot", "rug", "audit", "liquidity position", "lp range",
                   "lp position", "in range", "range check", "re-centre", "recenter", "grid", "health factor",
                   "liquidation", "borrow limit", "nft", "meme", "airdrop", "copy trade", "swap for me", "buy ", "sell ")
DEFAULT_TASK = "Stablecoin yield assessment on Venus (BNB Chain) for 1,000 USDT over 365 days"
ASSETS = ("USDT", "USDC", "FDUSD", "USD1", "DAI", "USDe", "BUSD", "TUSD", "lisUSD")
DEFAULT_AMOUNT = 1_000.0
SWAP_COST_PCT = 0.05
ALLOWED_GRADES = {"low": {"A"}, "medium": {"A", "B"}, "high": {"A", "B", "C"}}
SCOPE_HINT = ("Halong Yield answers stablecoin yield questions on BNB Chain (Venus and Aave V3). "
              "Ask for example: 'Where should I deposit 5,000 USDT for the best yield over 90 days?'")


def in_scope(task: str) -> bool:
    text = task.lower()
    if any(w in text for w in SCOPE_WORDS) or "$u" in text:
        return True
    return not any(w in text for w in OFF_TOPIC_WORDS)


def parse_brief(task: str) -> dict:
    """Deterministic reading of a brief: asset, amount, horizon, wallet, risk tolerance."""
    asset = None
    for sym in ASSETS:
        if re.search(rf"\b{re.escape(sym)}\b", task, re.IGNORECASE):
            asset = next(a for a in ASSETS if a.lower() == sym.lower())
            break
    if asset is None and re.search(r"\$U\b", task):
        asset = "U"
    amount, amount_stated = DEFAULT_AMOUNT, False
    horizon_re = r"(\d+)\s*(day|week|month|year)s?"
    money_text = re.sub(horizon_re, "", re.sub(r"0x[a-fA-F0-9]{40}", "", task), flags=re.IGNORECASE).replace("$", "")
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?\b", money_text, re.IGNORECASE)
    if m:
        value = float(m.group(1).replace(",", ""))
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((m.group(2) or "").lower(), 1)
        if value * mult >= 1:
            amount, amount_stated = value * mult, True
    days = 365
    h = re.search(horizon_re, task, re.IGNORECASE)
    if h:
        days = int(h.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[h.group(2).lower()]
    text = task.lower()
    tolerance = "low" if any(w in text for w in ("safe", "conservative", "low risk", "low-risk")) else \
                "high" if any(w in text for w in ("aggressive", "max yield", "highest yield", "high risk")) else "medium"
    wallet_m = re.search(r"0x[a-fA-F0-9]{40}", task)
    return {"asset": asset, "amount": amount, "amount_stated": amount_stated, "horizon_days": days,
            "risk_tolerance": tolerance,
            "needs_instant_liquidity": any(w in text for w in ("any time", "anytime", "instant", "withdraw at")),
            "wallet": Web3.to_checksum_address(wallet_m.group(0)) if wallet_m else None, "read_by": "rules"}


def understand(task: str, use_llm: bool) -> dict:
    brief = parse_brief(task)
    ai = llm.parse_brief(task) if use_llm else None
    if not ai:
        return brief
    if ai["assets"]:
        brief["asset"] = ai["assets"][0]
    if ai["amount_usd"] > 0:
        brief["amount"], brief["amount_stated"] = float(ai["amount_usd"]), True
    if ai["horizon_days"] > 0:
        brief["horizon_days"] = int(ai["horizon_days"])
    brief["risk_tolerance"] = ai["risk_tolerance"]
    brief["needs_instant_liquidity"] = bool(ai["needs_instant_liquidity"])
    if Web3.is_address(ai["wallet"] or ""):
        brief["wallet"] = Web3.to_checksum_address(ai["wallet"])
    brief["read_by"] = llm.MODEL
    return brief


def _row(m, a: dict, amount: float, days: int) -> dict:
    return {**m.as_dict(), "risk_adjusted_apy": a["risk_adjusted_apy"], "base_apy": a["base_apy"],
            "grade": a["grade"], "excluded": a["excluded"], "penalties": a["penalties"], "history_7d": a["history"],
            "expected_earnings": round(amount * m.supply_apy / 100 * days / 365, 2)}


def _answer(brief: dict, best: dict, asked_best: dict | None, candidates: list[dict]) -> tuple[str, dict]:
    amount, days, asset = brief["amount"], brief["horizon_days"], brief["asset"]
    if asset and asked_best is None:
        return (f"No {asset} market passes a {brief['risk_tolerance']}-risk filter for {amount:,.0f}. The best option is "
                f"{best['protocol']} {best['asset']} at {best['supply_apy']:.2f}% ({best['risk_adjusted_apy']:.2f}% risk-adjusted), "
                f"which means swapping first."), best
    if asset and asked_best is not best:
        spread = best["risk_adjusted_apy"] - asked_best["risk_adjusted_apy"]
        if spread > SWAP_COST_PCT * 365 / days:
            return (f"Swap {amount:,.0f} {asset} into {best['asset']} and supply it on {best['protocol']}: "
                    f"{best['supply_apy']:.2f}% APY ({best['risk_adjusted_apy']:.2f}% risk-adjusted), about "
                    f"{best['expected_earnings']:,.2f} over {days} days, against {asked_best['expected_earnings']:,.2f} "
                    f"for {asset} on {asked_best['protocol']}. The {spread:.2f} pp edge covers an assumed "
                    f"{SWAP_COST_PCT}% swap cost."), best
        best = asked_best
    others = [c for c in candidates if c["asset"] == best["asset"] and c["protocol"] != best["protocol"]]
    versus = (f", ahead of {others[0]['protocol']} at {others[0]['supply_apy']:.2f}%" if others else "")
    return (f"Supply {amount:,.0f} {best['asset']} on {best['protocol']}: {best['supply_apy']:.2f}% APY "
            f"({best['risk_adjusted_apy']:.2f}% risk-adjusted, grade {best['grade']}){versus}, about "
            f"{best['expected_earnings']:,.2f} {best['asset']} over {days} days at today's rate."), best


def _template_note(brief: dict, pick: dict, n_markets: int, n_pass: int, plan: dict | None) -> dict:
    note = (f"Compared {n_markets} stablecoin markets on Venus and Aave V3; {n_pass} pass a "
            f"{brief['risk_tolerance']}-risk filter for this amount. {pick['protocol']} {pick['asset']} ranks first on "
            f"risk-adjusted APY ({pick['risk_adjusted_apy']:.2f}%), with {pick['utilization']:.1f}% utilization and "
            f"{pick['liquidity']:,.0f} {pick['asset']} withdrawable today.")
    if plan and plan["moves"]:
        note += (f" For your wallet, {len(plan['moves'])} step(s) add about ${plan['proposed']['annual_gain_usd']:,.2f} "
                 f"a year for ${plan['proposed']['one_off_gas_usd']:.4f} of gas.")
    watch = ["Supply APYs move with borrow demand every block."]
    if pick["utilization"] > 80:
        watch.append(f"Utilization above 85% would start to slow withdrawals (now {pick['utilization']:.1f}%).")
    return {"headline": f"Best risk-adjusted home: {pick['protocol']} {pick['asset']}.", "note": note,
            "watch": watch, "by": "template"}


def assessment(task: str, use_llm: bool = False) -> dict:
    brief = understand(task or DEFAULT_TASK, use_llm)
    amount, days = brief["amount"], brief["horizon_days"]
    block, ms = markets.stable_markets()
    ranked = risk.rank(ms, amount)
    rows = [_row(m, a, amount, days) for m, a in ranked]
    allowed = ALLOWED_GRADES[brief["risk_tolerance"]]
    candidates = [r for r in rows if r["excluded"] is None and r["grade"] in allowed
                  and (not brief["needs_instant_liquidity"] or r["utilization"] < 85)]
    best = candidates[0] if candidates else rows[0]
    asked_best = next((r for r in candidates if r["asset"] == brief["asset"]), None) if brief["asset"] else None
    answer, pick = _answer(brief, best, asked_best, candidates)

    plan = None
    if brief["wallet"]:
        plan = wallet.optimize(brief["wallet"], ranked, days)
        if not brief["amount_stated"]:
            cur, new = plan["current"], plan["proposed"]
            answer = (f"Your wallet holds ${cur['value_usd']:,.2f} in dollar stablecoins earning {cur['blended_apy']:.2f}% "
                      f"blended. {len(plan['moves'])} step(s) below raise that to {new['blended_apy']:.2f}% "
                      f"(+${new['annual_gain_usd']:,.2f} a year) for ${new['one_off_gas_usd']:.4f} of gas."
                      if plan["moves"] else
                      f"Your wallet holds ${cur['value_usd']:,.2f} in dollar stablecoins earning {cur['blended_apy']:.2f}% "
                      f"blended; nothing to move right now. Best market for new deposits: {answer}")

    facts = {"brief": brief, "answer": answer, "pick": {k: pick[k] for k in (
                 "protocol", "asset", "supply_apy", "risk_adjusted_apy", "grade", "utilization", "liquidity",
                 "expected_earnings", "penalties")},
             "alternatives": [{k: r[k] for k in ("protocol", "asset", "supply_apy", "risk_adjusted_apy", "grade")}
                              for r in candidates[:5] if r is not pick],
             "markets_compared": len(rows), "markets_passing_filter": len(candidates), "mainnet_block": block,
             "wallet_plan": {k: plan[k] for k in ("current", "proposed")} if plan else None}
    note = (llm.analyst_note(facts) if use_llm else None) or _template_note(brief, pick, len(rows), len(candidates), plan)

    report = {
        "agent": {"name": settings.AGENT_NAME, "category": settings.CATEGORY,
                  "erc8004": f"eip155:{settings.CHAIN_ID}:{settings.IDENTITY_REGISTRY}/{settings.AGENT_ID}"},
        "brief": {"task": task, **brief},
        "answer": answer,
        "analyst_note": note,
        "recommendation": {"protocol": pick["protocol"], "market": pick["market"], "asset": pick["asset"],
                           "supply_apy": pick["supply_apy"], "risk_adjusted_apy": pick["risk_adjusted_apy"],
                           "grade": pick["grade"], "expected_earnings": pick["expected_earnings"],
                           "penalties": pick["penalties"]},
        "markets": rows,
        "wallet_plan": plan,
        "method": [
            "Supply APY: Venus compounds supplyRatePerBlock daily over blocks per year measured from recent blocks; "
            "Aave compounds the per-second liquidity rate.",
            "Risk-adjusted APY = base APY (spot, blended with the 7-day average when history exists) minus itemized "
            "penalties for volatility, utilization, exit liquidity, oracle depeg and issuer tier.",
            f"Risk filter '{brief['risk_tolerance']}' admits grades {', '.join(sorted(allowed))}"
            + ("; instant liquidity requires utilization under 85%." if brief["needs_instant_liquidity"] else "."),
            f"Expected earnings hold today's rate constant for {days} days; swaps assume a {SWAP_COST_PCT}% cost.",
        ],
        "risks": [
            "Supply APY is variable: it moves with borrow demand every block.",
            "Smart-contract and oracle risk of Venus and Aave V3.",
            "Stablecoin issuer and depeg risk, higher for smaller stablecoins.",
            "High utilization can delay withdrawals until borrowers repay.",
        ],
        "data_sources": {"chain_id": 56, "block": block, "venus_comptroller": venus.MAINNET_COMPTROLLER,
                         "aave_v3_pool": aave.POOL, "rpc": settings.MAINNET_RPC, "read_at": int(time.time())},
    }
    try:
        report["agent_treasury"] = strategy.treasury_snapshot()
    except Exception:
        pass
    return report


def preview(task: str = "") -> dict:
    block, ms = markets.stable_markets()
    top = [(m, a) for m, a in risk.rank(ms, DEFAULT_AMOUNT) if a["excluded"] is None][:3]
    return {
        "can_answer": in_scope(task) if task else True,
        "hint": None if (not task or in_scope(task)) else SCOPE_HINT,
        "top_markets": [{"protocol": m.protocol, "asset": m.asset, "supply_apy": round(m.supply_apy, 3),
                         "risk_adjusted_apy": a["risk_adjusted_apy"], "grade": a["grade"],
                         "utilization": round(m.utilization, 1)} for m, a in top],
        "markets_compared": len(ms),
        "block": block,
        "a_hire_returns": ("A JSON assessment for your amount, horizon and risk tolerance: every stablecoin market on "
                           "Venus and Aave V3 ranked by risk-adjusted APY with itemized penalties, expected earnings, "
                           "whether a swap pays for itself, an analyst note, and, if you give a wallet address, a "
                           "step-by-step plan with unsigned transactions."),
        "price": f"{settings.JOB_PRICE_WEI / 1e18:g} U per assessment, ERC-8183 escrow on BSC testnet (chain 97)",
    }
