"""Treasury cycle: a stablecoin treasury on Venus (BSC testnet) kept at weights set by mainnet data.

Testnet Venus has no borrowers, so its own APYs (~0) carry no signal. Each cycle:

1. For USDT / USDC / BUSD, find the best mainnet market (Venus or Aave V3) by risk-adjusted APY.
2. Weight each asset by that risk-adjusted APY (assets with no eligible market get zero).
3. Grow the deployed target by 5% (simulated deposits, minted from the Venus testnet faucet)
   and set each asset's target position = weight x target.
4. Redeem what sits above target, top up and supply what sits below, beyond a small tolerance.
5. Sign a decision receipt with the inputs, weights, positions before/after and every
   transaction, and pin it to IPFS.

Runs twice a day from GitHub Actions (.github/workflows/cycle.yml), with a daily Vercel Cron backup.
"""

import logging

from web3 import Web3

from . import chain, markets, receipts, risk, settings, venus

log = logging.getLogger("halong.strategy")

BASE_SIZE = 1_000            # deployed target floor, in whole tokens
GROWTH = 1.05                # target grows 5% per cycle
MAX_SIZE = 5_000
TOLERANCE = 0.02             # of the target: smaller gaps are left alone
MAX_STEP = 400               # whole tokens moved per asset per cycle
MIN_GAS_WEI = Web3.to_wei(0.003, "ether")


def signal() -> tuple[int, dict[str, dict]]:
    """Best mainnet market per treasury asset, assessed at treasury size."""
    block, ms = markets.stable_markets()
    best: dict[str, dict] = {}
    for m, a in risk.rank(ms, BASE_SIZE):
        if m.asset in venus.TESTNET_TREASURY and m.asset not in best:
            best[m.asset] = {"protocol": m.protocol, "spot_apy": round(m.supply_apy, 4),
                             "risk_adjusted_apy": a["risk_adjusted_apy"], "grade": a["grade"],
                             "excluded": a["excluded"]}
    for asset in venus.TESTNET_TREASURY:
        best.setdefault(asset, {"protocol": None, "spot_apy": 0.0, "risk_adjusted_apy": 0.0, "grade": None,
                                "excluded": "no mainnet market"})
    return block, best


def weights(best: dict[str, dict]) -> dict[str, float]:
    scores = {a: (b["risk_adjusted_apy"] if b["excluded"] is None else 0.0) for a, b in best.items()}
    scores = {a: max(s, 0.0) for a, s in scores.items()}
    total = sum(scores.values())
    return {a: (s / total if total else 0.0) for a, s in scores.items()}


def _units(b: dict, key: str) -> float:
    return b[key] / 10**b["decimals"]


def run_cycle() -> dict:
    owner = chain.agent_account().address
    if chain.testnet().eth.get_balance(owner) < MIN_GAS_WEI:
        raise RuntimeError(f"agent wallet {owner} is below the gas reserve; top it up with tBNB")
    block, best = signal()
    w = weights(best)
    before = venus.treasury_balances(owner)
    total = sum(_units(b, "idle") + _units(b, "supplied") for b in before.values())
    target = min(MAX_SIZE, max(BASE_SIZE, sum(_units(b, "supplied") for b in before.values())) * GROWTH)
    actions = []

    def act(kind: str, detail: str, tx: str):
        log.info("%s: %s %s/tx/%s", kind, detail, settings.EXPLORER, tx)
        actions.append({"kind": kind, "detail": detail, "tx": tx})

    # Redeem first so freed funds are visible before deposits.
    for asset, b in before.items():
        gap = w[asset] * target - _units(b, "supplied")
        if gap < -TOLERANCE * target:
            amount = min(-gap, MAX_STEP, _units(b, "supplied"))
            raw = int(amount * 10**b["decimals"])
            act("redeem", f"redeemed {amount:,.2f} {asset}: above its {w[asset]:.0%} target weight",
                venus.redeem(asset, raw))
    for asset, b in before.items():
        gap = w[asset] * target - _units(b, "supplied")
        if gap > TOLERANCE * target:
            amount = min(gap, MAX_STEP)
            raw = int(amount * 10**b["decimals"])
            if b["idle"] < raw:
                act("float", f"minted {(raw - b['idle']) / 10**b['decimals']:,.2f} test {asset} (simulated deposits)",
                    venus.faucet(asset, raw - b["idle"]))
            act("supply", f"supplied {amount:,.2f} {asset} toward its {w[asset]:.0%} target weight "
                          f"({best[asset]['protocol']} leads on mainnet at {best[asset]['risk_adjusted_apy']:.2f}% risk-adjusted)",
                venus.supply(asset, raw))

    after = venus.treasury_balances(owner)
    receipt = receipts.sign(receipts.new(
        "treasury-cycle",
        inputs={"mainnet_block": block, "best_market_per_asset": best},
        decision={"weights": {a: round(x, 4) for a, x in w.items()}, "target_deployed": round(target, 2),
                  "treasury_value_before": round(total, 2)},
        positions={"before": {a: {"idle": round(_units(b, "idle"), 2), "supplied": round(_units(b, "supplied"), 2)}
                              for a, b in before.items()},
                   "after": {a: {"idle": round(_units(b, "idle"), 2), "supplied": round(_units(b, "supplied"), 2)}
                             for a, b in after.items()}},
        actions=actions,
    ))
    try:
        url = receipts.pin(receipt)
    except Exception:
        log.exception("pinning the receipt failed; it is still returned and indexed")
        url = None
    return {"receipt": receipt, "ipfs": url}


def treasury_snapshot() -> dict:
    owner = chain.agent_account().address
    bal = venus.treasury_balances(owner)
    return {
        "wallet": owner,
        "chain_id": settings.CHAIN_ID,
        "positions": {a: {"idle": round(_units(b, "idle"), 2), "supplied_to_venus": round(_units(b, "supplied"), 2)}
                      for a, b in bal.items()},
        "activity": f"{settings.EXPLORER}/address/{owner}",
    }
