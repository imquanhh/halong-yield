"""Risk-adjusted APY: the headline APY minus explicit, itemized penalties.

Every penalty is in percentage points and carries its reason, so a hirer can see exactly why
a market ranks where it does:

- base APY blends spot with the 7-day average when history exists (half each), so one
  block's spike cannot top the table;
- volatility: half the 7-day standard deviation;
- utilization above 85%: withdrawals depend on borrowers repaying;
- exit liquidity: the larger the amount relative to what is withdrawable now, the higher the
  penalty; below 2x cover the market is excluded;
- depeg: oracle price away from $1 (excluded beyond 2%);
- issuer tier: USDT/USDC carry none, smaller or synthetic dollars carry more.
"""

from . import history
from .model import Market

ISSUER_TIER = {"USDT": 0, "USDC": 0, "FDUSD": 1, "USD1": 1, "DAI": 1, "U": 2, "USDe": 2, "lisUSD": 2,
               "TUSD": 3, "BUSD": 3}
TIER_PENALTY = {0: 0.0, 1: 0.25, 2: 0.6, 3: 1.5}
TIER_NOTE = {1: "smaller issuer", 2: "newer or synthetic dollar", 3: "deprecated or thinly backed stablecoin"}


def assess(m: Market, amount: float) -> dict:
    penalties: list[dict] = []
    excluded = None

    def penalize(pp: float, reason: str):
        if pp > 0.005:
            penalties.append({"pp": round(pp, 3), "reason": reason})

    hist = history.stats(m.key)
    base = m.supply_apy if hist is None else 0.5 * m.supply_apy + 0.5 * hist["avg"]
    if hist is not None:
        penalize(0.5 * hist["stdev"], f"APY swung {hist['min']:.2f}-{hist['max']:.2f}% over 7 days")

    if m.supply_apy <= 0:
        excluded = "pays nothing: no borrow demand"
    if m.utilization > 85:
        penalize((m.utilization - 85) * 0.08, f"utilization {m.utilization:.1f}%: exits depend on repayments")
    if m.liquidity < amount * 2:
        excluded = excluded or f"only {m.liquidity:,.0f} {m.asset} withdrawable for a {amount:,.0f} position"
    else:
        share = amount / m.liquidity
        if share > 0.01:
            penalize(min(2.0, share * 20), f"position is {share:.1%} of withdrawable liquidity")
    if m.price is not None:
        dev = abs(m.price - 1) * 100
        if dev > 2:
            excluded = excluded or f"depegged: oracle price ${m.price:.4f}"
        elif dev > 0.2:
            penalize(dev, f"oracle price ${m.price:.4f}, {dev:.2f}% off the peg")
    tier = ISSUER_TIER.get(m.asset, 3)
    if tier:
        penalize(TIER_PENALTY[tier], TIER_NOTE[tier])

    total = sum(p["pp"] for p in penalties)
    grade = "A" if total < 0.3 else "B" if total < 1.0 else "C"
    return {
        "key": m.key,
        "base_apy": round(base, 4),
        "risk_adjusted_apy": round(base - total, 4),
        "grade": None if excluded else grade,
        "excluded": excluded,
        "penalties": penalties,
        "history": {k: round(v, 4) if isinstance(v, float) else v for k, v in hist.items()} if hist else None,
    }


def rank(markets: list[Market], amount: float) -> list[tuple[Market, dict]]:
    """Markets with their assessment, eligible ones first, best risk-adjusted APY first."""
    scored = [(m, assess(m, amount)) for m in markets]
    return sorted(scored, key=lambda ma: (ma[1]["excluded"] is None, ma[1]["risk_adjusted_apy"]), reverse=True)
