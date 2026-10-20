"""Treasury cycle: keep the agent's stablecoins earning on Venus (BSC testnet), steered by mainnet data.

Testnet Venus markets have almost no borrowers, so their own APYs are ~0 and say nothing.
The signal is therefore the mainnet ranking of the same assets; the testnet treasury acts on it:

1. rank USDT / USDC / BUSD by mainnet supply APY, skipping markets that are too
   utilized or too thin to exit from;
2. top up the working float of the leader from the testnet faucet if it runs low;
3. deposit a share of the leader's idle float into its Venus market;
4. pull a share back from any supplied market the leader beats by more than the spread.

Runs twice a day from GitHub Actions (.github/workflows/cycle.yml).
"""

import logging

from web3 import Web3

from . import chain, settings, venus

log = logging.getLogger("halong.strategy")

FLOAT_TARGET = 500          # whole tokens of the leading asset kept on hand for deposits
DEPOSIT_SHARE = 0.4         # of the leader's idle float, supplied each cycle
ROTATE_SHARE = 0.5          # of a lagging market's position, redeemed each cycle
ROTATE_SPREAD = 0.25        # percentage points the leader must beat a market by
MAX_UTILIZATION = 92.0      # percent; above this, withdrawals can queue
MIN_CASH = 1_000_000        # asset units of idle liquidity a market needs to qualify
MIN_GAS_WEI = Web3.to_wei(0.003, "ether")


def rank_signal() -> tuple[int, list[dict]]:
    """Treasury assets ranked by mainnet supply APY, with why a market was excluded."""
    block, markets = venus.mainnet_stable_markets()
    by_asset = {m.asset: m for m in markets}
    ranked = []
    for asset in venus.TESTNET_TREASURY:
        m = by_asset.get(asset)
        if m is None:
            ranked.append({"asset": asset, "apy": 0.0, "eligible": False, "reason": "no mainnet market data"})
        elif m.utilization > MAX_UTILIZATION:
            ranked.append({"asset": asset, "apy": m.supply_apy, "eligible": False,
                           "reason": f"utilization {m.utilization:.1f}% > {MAX_UTILIZATION}%"})
        elif m.cash < MIN_CASH:
            ranked.append({"asset": asset, "apy": m.supply_apy, "eligible": False,
                           "reason": f"only {m.cash:,.0f} {asset} withdrawable"})
        else:
            ranked.append({"asset": asset, "apy": m.supply_apy, "eligible": True, "reason": "ok"})
    ranked.sort(key=lambda r: (r["eligible"], r["apy"]), reverse=True)
    return block, ranked


def _fmt(raw: int, decimals: int) -> str:
    return f"{raw / 10**decimals:,.2f}"


def _action(kind: str, detail: str, tx: str) -> dict:
    log.info("%s: %s %s/tx/%s", kind, detail, settings.EXPLORER, tx)
    return {"kind": kind, "detail": detail, "tx": tx}


def run_cycle() -> list[dict]:
    owner = chain.agent_account().address
    if chain.testnet().eth.get_balance(owner) < MIN_GAS_WEI:
        raise RuntimeError(f"agent wallet {owner} is below the gas reserve; top it up with tBNB")
    block, ranked = rank_signal()
    leader = ranked[0]
    if not leader["eligible"]:
        log.warning("no eligible market this cycle: %s", ranked)
        return []
    apy = {r["asset"]: r["apy"] for r in ranked}
    asset = leader["asset"]
    bal = venus.treasury_balances(owner)
    dec = bal[asset]["decimals"]
    actions = []

    if bal[asset]["idle"] < FLOAT_TARGET * 10**dec // 2:
        amount = FLOAT_TARGET * 10**dec - bal[asset]["idle"]
        actions.append(_action("float", f"minted {_fmt(amount, dec)} test {asset} for deposits",
                               venus.faucet(asset, amount)))
        bal[asset]["idle"] += amount

    deposit = int(bal[asset]["idle"] * DEPOSIT_SHARE)
    if deposit > 0:
        actions.append(_action(
            "supply", f"supplied {_fmt(deposit, dec)} {asset} to Venus v{asset} "
                      f"(mainnet leader at {leader['apy']:.2f}% APY, block {block})", venus.supply(asset, deposit)))

    for other, b in bal.items():
        spread = leader["apy"] - apy[other]
        if other == asset or b["supplied"] == 0 or spread <= ROTATE_SPREAD:
            continue
        amount = int(b["supplied"] * ROTATE_SHARE)
        actions.append(_action(
            "redeem", f"redeemed {_fmt(amount, b['decimals'])} {other} from Venus: {asset} pays "
                      f"{spread:.2f} pp more on mainnet", venus.redeem(other, amount)))
    return actions


def treasury_snapshot() -> dict:
    owner = chain.agent_account().address
    bal = venus.treasury_balances(owner)
    return {
        "wallet": owner,
        "chain_id": settings.CHAIN_ID,
        "positions": {a: {"idle": round(b["idle"] / 10**b["decimals"], 2),
                          "supplied_to_venus": round(b["supplied"] / 10**b["decimals"], 2)}
                      for a, b in bal.items()},
        "activity": f"{settings.EXPLORER}/address/{owner}",
    }
