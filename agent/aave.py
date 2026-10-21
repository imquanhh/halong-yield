"""Aave V3 on BNB Chain (mainnet): stablecoin reserve reads.

Supply APY compounds the per-second liquidity rate (ray, 1e27) the way Aave's UI does:
(1 + rate / secondsPerYear) ** secondsPerYear - 1. Withdrawable liquidity is the underlying
held by the aToken; prices come from the AaveOracle (8-decimal USD).
"""

import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

from . import chain
from .model import Market

POOL = Web3.to_checksum_address("0x6807dc923806fE8Fd134338EABCA509979a7e0cB")
STABLES = {"USDT", "USDC", "FDUSD", "USD1", "DAI", "TUSD", "lisUSD", "U", "USDe"}
SECONDS_PER_YEAR = 31_536_000
CACHE_SECONDS = 300

RESERVE_TUPLE = {"type": "tuple", "components": [
    {"name": "configuration", "type": "uint256"}, {"name": "liquidityIndex", "type": "uint128"},
    {"name": "currentLiquidityRate", "type": "uint128"}, {"name": "variableBorrowIndex", "type": "uint128"},
    {"name": "currentVariableBorrowRate", "type": "uint128"}, {"name": "currentStableBorrowRate", "type": "uint128"},
    {"name": "lastUpdateTimestamp", "type": "uint40"}, {"name": "id", "type": "uint16"},
    {"name": "aTokenAddress", "type": "address"}, {"name": "stableDebtTokenAddress", "type": "address"},
    {"name": "variableDebtTokenAddress", "type": "address"}, {"name": "interestRateStrategyAddress", "type": "address"},
    {"name": "accruedToTreasury", "type": "uint128"}, {"name": "unbacked", "type": "uint128"},
    {"name": "isolationModeTotalDebt", "type": "uint128"}]}
POOL_ABI = [
    {"type": "function", "name": "getReservesList", "stateMutability": "view", "inputs": [],
     "outputs": [{"type": "address[]"}]},
    {"type": "function", "name": "getReserveData", "stateMutability": "view",
     "inputs": [{"name": "asset", "type": "address"}], "outputs": [RESERVE_TUPLE]},
    {"type": "function", "name": "ADDRESSES_PROVIDER", "stateMutability": "view", "inputs": [],
     "outputs": [{"type": "address"}]},
    {"type": "function", "name": "supply", "stateMutability": "nonpayable",
     "inputs": [{"name": "asset", "type": "address"}, {"name": "amount", "type": "uint256"},
                {"name": "onBehalfOf", "type": "address"}, {"name": "referralCode", "type": "uint16"}], "outputs": []},
    {"type": "function", "name": "withdraw", "stateMutability": "nonpayable",
     "inputs": [{"name": "asset", "type": "address"}, {"name": "amount", "type": "uint256"},
                {"name": "to", "type": "address"}], "outputs": [{"type": "uint256"}]},
]
PROVIDER_ABI = [{"type": "function", "name": "getPriceOracle", "stateMutability": "view", "inputs": [],
                 "outputs": [{"type": "address"}]}]
ORACLE_ABI = [{"type": "function", "name": "getAssetPrice", "stateMutability": "view",
               "inputs": [{"name": "asset", "type": "address"}], "outputs": [{"type": "uint256"}]}]
ERC20_ABI = [
    {"type": "function", "name": n, "stateMutability": "view", "inputs": [], "outputs": [{"type": t}]}
    for n, t in (("symbol", "string"), ("decimals", "uint8"), ("totalSupply", "uint256"))
] + [{"type": "function", "name": "balanceOf", "stateMutability": "view",
      "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]}]


def apy_percent(liquidity_rate_ray: int) -> float:
    return ((1 + liquidity_rate_ray / 1e27 / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - 1) * 100


def pool():
    return chain.mainnet().eth.contract(POOL, abi=POOL_ABI)


_reserves: list[tuple[str, str]] = []   # (underlying, symbol) of the stablecoin reserves
_oracle: str | None = None
_cache: tuple[float, list[Market]] = (0.0, [])


def _discover() -> None:
    global _reserves, _oracle
    w3 = chain.mainnet()
    p = pool()
    found = []
    for addr in p.functions.getReservesList().call():
        sym = w3.eth.contract(addr, abi=ERC20_ABI).functions.symbol().call()
        if sym in STABLES:
            found.append((addr, sym))
    provider = w3.eth.contract(p.functions.ADDRESSES_PROVIDER().call(), abi=PROVIDER_ABI)
    _oracle = provider.functions.getPriceOracle().call()
    _reserves = found


def read_reserve(underlying: str, symbol: str) -> Market:
    w3 = chain.mainnet()
    d = pool().functions.getReserveData(underlying).call()
    token = w3.eth.contract(underlying, abi=ERC20_ABI)
    decimals = token.functions.decimals().call()
    available = token.functions.balanceOf(d[8]).call()
    supplied = w3.eth.contract(d[8], abi=ERC20_ABI).functions.totalSupply().call()
    price = w3.eth.contract(_oracle, abi=ORACLE_ABI).functions.getAssetPrice(underlying).call() / 1e8
    scale = 10**decimals
    return Market(
        protocol="Aave V3", chain_id=56, asset=symbol, underlying=underlying, market=d[8], decimals=decimals,
        supply_apy=apy_percent(d[2]), borrow_apy=apy_percent(d[4]),
        utilization=(1 - available / supplied) * 100 if supplied else 0.0,
        liquidity=available / scale, total_supplied=supplied / scale, price=price,
    )


def mainnet_stable_markets() -> list[Market]:
    global _cache
    fetched_at, markets = _cache
    if markets and time.time() - fetched_at < CACHE_SECONDS:
        return markets
    if not _reserves:
        _discover()

    def read(r):
        for attempt in range(3):
            try:
                return read_reserve(*r)
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(1 + attempt)

    with ThreadPoolExecutor(max_workers=4) as ex:
        markets = [m for m in ex.map(read, _reserves) if m is not None]
    _cache = (time.time(), markets)
    return markets
