"""Wallet optimizer: what a BNB Chain wallet holds in dollar stablecoins, what it earns today,
and a step-by-step plan to earn more, with the unsigned transactions for each step.

Read-only: balances and positions are read from mainnet; calldata is returned for the owner
to review and sign in their own wallet. The agent never signs for a hirer.
"""

from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

from . import aave, chain, venus
from .model import Market

WBNB = Web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
MIN_USD = 10                 # ignore dust
SAME_ASSET_SPREAD = 0.15     # pp a better market must pay before moving between protocols
CAP_SHARE = 0.02             # never put more than 2% of a market's withdrawable liquidity into it
SWAP_COST_PCT = 0.05         # assumed round-trip cost of swapping between dollar stablecoins
GAS = {"approve": 50_000, "supply": 250_000, "withdraw": 300_000}
MAX_UINT = 2**256 - 1


def _raw_position(w3: Web3, m: Market, wallet: str) -> int:
    if m.protocol == "Venus":
        v = w3.eth.contract(m.market, abi=venus.VTOKEN_ABI)
        return v.functions.balanceOf(wallet).call() * v.functions.exchangeRateStored().call() // 10**18
    return w3.eth.contract(m.market, abi=aave.ERC20_ABI).functions.balanceOf(wallet).call()


def scan(wallet: str, markets: list[Market]) -> dict:
    """Idle stablecoin balances and lending positions, in raw units and USD."""
    w3 = chain.mainnet()
    tokens = {}
    for m in markets:
        tokens.setdefault(m.asset, m)

    def idle(asset_market):
        asset, m = asset_market
        raw = w3.eth.contract(m.underlying, abi=venus.ERC20_ABI).functions.balanceOf(wallet).call()
        return asset, m, raw

    with ThreadPoolExecutor(max_workers=4) as ex:
        idle_rows = list(ex.map(idle, tokens.items()))
        pos_rows = list(ex.map(lambda m: (m, _raw_position(w3, m, wallet)), markets))

    def row(m, raw):
        amount = raw / 10**m.decimals
        return {"asset": m.asset, "raw": raw, "amount": round(amount, 2), "usd": round(amount * (m.price or 1), 2)}

    return {
        "idle": [row(m, raw) for _, m, raw in idle_rows if raw and raw / 10**m.decimals * (m.price or 1) >= MIN_USD],
        "positions": [{**row(m, raw), "protocol": m.protocol, "key": m.key, "supply_apy": round(m.supply_apy, 3)}
                      for m, raw in pos_rows if raw and raw / 10**m.decimals * (m.price or 1) >= MIN_USD],
    }


def _supply_calls(m: Market, raw: int, wallet: str) -> list[dict]:
    w3 = chain.mainnet()
    token = w3.eth.contract(m.underlying, abi=venus.ERC20_ABI)
    if m.protocol == "Venus":
        return [
            {"to": m.underlying, "data": token.encode_abi("approve", [m.market, raw]),
             "does": f"approve Venus v{m.asset} to take {raw / 10**m.decimals:,.2f} {m.asset}"},
            {"to": m.market, "data": w3.eth.contract(m.market, abi=venus.VTOKEN_ABI).encode_abi("mint", [raw]),
             "does": f"supply {raw / 10**m.decimals:,.2f} {m.asset} to Venus"},
        ]
    pool = aave.pool()
    return [
        {"to": m.underlying, "data": token.encode_abi("approve", [aave.POOL, raw]),
         "does": f"approve the Aave V3 Pool to take {raw / 10**m.decimals:,.2f} {m.asset}"},
        {"to": aave.POOL, "data": pool.encode_abi("supply", [m.underlying, raw, wallet, 0]),
         "does": f"supply {raw / 10**m.decimals:,.2f} {m.asset} to Aave V3"},
    ]


def _withdraw_calls(m: Market, raw: int, wallet: str) -> list[dict]:
    w3 = chain.mainnet()
    if m.protocol == "Venus":
        return [{"to": m.market, "data": w3.eth.contract(m.market, abi=venus.VTOKEN_ABI).encode_abi("redeemUnderlying", [raw]),
                 "does": f"withdraw {raw / 10**m.decimals:,.2f} {m.asset} from Venus"}]
    return [{"to": aave.POOL, "data": aave.pool().encode_abi("withdraw", [m.underlying, MAX_UINT, wallet]),
             "does": f"withdraw all {m.asset} from Aave V3"}]


def _gas_usd(units: int) -> float:
    w3 = chain.mainnet()
    try:
        bnb_usd = w3.eth.contract(aave._oracle, abi=aave.ORACLE_ABI).functions.getAssetPrice(WBNB).call() / 1e8
    except Exception:
        bnb_usd = 600.0
    return units * w3.eth.gas_price / 1e18 * bnb_usd


def _capacity_usd(m: Market) -> float:
    return m.liquidity * (m.price or 1) * CAP_SHARE


def optimize(wallet: str, ranked: list[tuple[Market, dict]], horizon_days: int) -> dict:
    markets = [m for m, _ in ranked]
    holdings = scan(wallet, markets)
    by_key = {m.key: m for m in markets}
    # Candidate markets per asset, best risk-adjusted first. Exit-liquidity exclusions are recomputed
    # per position below (CAP_SHARE), so only markets excluded for other reasons are dropped here.
    usable = [m for m, a in ranked if a["excluded"] is None or a["excluded"].startswith("only ")]
    room = {m.key: _capacity_usd(m) for m in usable}
    moves, gas_units, left_idle = [], 0, []

    def allocate(asset: str, usd: float, raw: int, beat_apy: float, skip_key: str | None):
        """Split a holding across same-asset markets that pay more than beat_apy, within capacity."""
        parts, remaining = [], usd
        for m in usable:
            if m.asset != asset or m.key == skip_key or m.supply_apy - beat_apy < SAME_ASSET_SPREAD:
                continue
            take = min(remaining, room[m.key])
            if take < MIN_USD:
                continue
            room[m.key] -= take
            parts.append((m, int(raw * take / usd), take))
            remaining -= take
            if remaining < MIN_USD:
                break
        return parts, remaining

    for h in holdings["idle"]:
        parts, remaining = allocate(h["asset"], h["usd"], h["raw"], beat_apy=0.0, skip_key=None)
        for m, raw, usd in parts:
            moves.append({"kind": "put idle funds to work", "asset": h["asset"], "amount": round(raw / 10**m.decimals, 2),
                          "from": "wallet (0%)", "to": f"{m.protocol} {m.asset} ({m.supply_apy:.2f}%)",
                          "annual_gain_usd": round(usd * m.supply_apy / 100, 2), "calls": _supply_calls(m, raw, wallet)})
            gas_units += GAS["approve"] + GAS["supply"]
        if remaining >= MIN_USD and parts:
            left_idle.append({"asset": h["asset"], "usd": round(remaining, 2)})

    for p in holdings["positions"]:
        current = by_key[p["key"]]
        parts, _ = allocate(p["asset"], p["usd"], p["raw"], beat_apy=current.supply_apy, skip_key=current.key)
        for m, raw, usd in parts:
            moves.append({"kind": "move to a better market for the same asset", "asset": p["asset"],
                          "amount": round(raw / 10**m.decimals, 2),
                          "from": f"{current.protocol} ({current.supply_apy:.2f}%)", "to": f"{m.protocol} ({m.supply_apy:.2f}%)",
                          "annual_gain_usd": round(usd * (m.supply_apy - current.supply_apy) / 100, 2),
                          "calls": _withdraw_calls(current, raw, wallet) + _supply_calls(m, raw, wallet)})
            gas_units += GAS["withdraw"] + GAS["approve"] + GAS["supply"]

    # A cross-asset swap is only suggested, never encoded: routes and slippage need the owner's eyes.
    swaps = []
    eligible = [m for m, a in ranked if a["excluded"] is None]
    if eligible:
        top = eligible[0]
        swap_room = room.get(top.key, 0.0)
        for p in holdings["positions"] + [dict(h, supply_apy=0.0) for h in holdings["idle"]]:
            same = next((m for m in eligible if m.asset == p["asset"]), None)
            from_apy = same.supply_apy if same else p.get("supply_apy", 0.0)
            spread = top.supply_apy - from_apy
            amount_usd = min(p["usd"], swap_room)
            if p["asset"] != top.asset and spread > SWAP_COST_PCT * 365 / horizon_days and amount_usd >= MIN_USD:
                swap_room -= amount_usd
                swaps.append({"asset": p["asset"], "usd": round(amount_usd, 2), "into": top.asset, "via": top.protocol,
                              "extra_apy_pp": round(spread, 3),
                              "annual_gain_usd": round(amount_usd * spread / 100
                                                       - amount_usd * SWAP_COST_PCT / 100 * 365 / horizon_days, 2)})

    value = sum(h["usd"] for h in holdings["idle"]) + sum(p["usd"] for p in holdings["positions"])
    earning = sum(p["usd"] * p["supply_apy"] / 100 for p in holdings["positions"])
    gain = sum(mv["annual_gain_usd"] for mv in moves)
    cost = _gas_usd(gas_units) if moves else 0.0
    notes = ["Transactions are unsigned: check each one in your wallet before signing.",
             f"No market receives more than {CAP_SHARE:.0%} of what it can pay out today, so you can always exit.",
             f"Gas is estimated from the current BNB Chain gas price; swaps assume a {SWAP_COST_PCT}% cost."]
    if left_idle:
        notes.append("Too large for the markets to absorb safely, left idle: "
                     + ", ".join(f"${x['usd']:,.0f} {x['asset']}" for x in left_idle) + ".")
    return {
        "wallet": wallet,
        "holdings": {"idle": [{k: v for k, v in h.items() if k != "raw"} for h in holdings["idle"]],
                     "positions": [{k: v for k, v in p.items() if k != "raw"} for p in holdings["positions"]]},
        "current": {"value_usd": round(value, 2), "annual_earnings_usd": round(earning, 2),
                    "blended_apy": round(earning / value * 100, 3) if value else 0.0},
        "proposed": {"annual_gain_usd": round(gain, 2),
                     "blended_apy": round((earning + gain) / value * 100, 3) if value else 0.0,
                     "one_off_gas_usd": round(cost, 4),
                     "break_even_days": round(cost / (gain / 365), 2) if gain > 0 else None},
        "moves": moves,
        "optional_swaps": swaps,
        "notes": notes,
    }
