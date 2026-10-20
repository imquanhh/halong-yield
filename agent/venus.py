"""Venus Core Pool: market reads (mainnet for analysis, testnet for the treasury) and supply/redeem.

APY follows the Venus UI convention, daily compounding of the per-block rate:
((ratePerBlock * blocksPerDay + 1) ** 365 - 1). Interest accrues per block, so blocksPerYear
is measured from the chain's recent block times rather than taken from the interest rate
model, whose constant can lag a block-time change (models still say 42M while BNB Chain now
produces ~70M blocks a year).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from web3 import Web3

from . import chain

MAINNET_COMPTROLLER = Web3.to_checksum_address("0xfD36E2c2a6789Db23113685031d7F16329158384")
# vToken symbols of the dollar stablecoin markets the analysis covers.
STABLE_VTOKENS = {"vUSDT", "vUSDC", "vFDUSD", "vUSD1", "vDAI", "vTUSD", "vlisUSD", "vU", "vUSDe", "vBUSD"}

# The treasury's markets on BSC testnet; their underlyings have a public allocateTo faucet.
TESTNET_TREASURY = {
    "USDT": {"vtoken": "0xb7526572FFE56AB9D7489838Bf2E18e3323b441A", "token": "0xA11c8D9DC9b66E209Ef60F0C8D969D3CD988782c"},
    "USDC": {"vtoken": "0xD5C4C2e2facBEB59D0216D0595d63FcDc6F9A1a7", "token": "0x16227D60f7a0e586C66B005219dfc887D13C9531"},
    "BUSD": {"vtoken": "0x08e0A5575De71037aE36AbfAfb516595fE68e5e4", "token": "0x8301F2213c0eeD49a7E28Ae4c3e91722919B8B47"},
}

CACHE_SECONDS = 300
BLOCK_SAMPLE = 20_000


def _views(*names_types):
    return [{"type": "function", "name": n, "stateMutability": "view", "inputs": [], "outputs": [{"type": t}]}
            for n, t in names_types]


VTOKEN_ABI = _views(("symbol", "string"), ("underlying", "address"), ("supplyRatePerBlock", "uint256"),
                    ("borrowRatePerBlock", "uint256"), ("getCash", "uint256"), ("totalBorrows", "uint256"),
                    ("totalReserves", "uint256"), ("exchangeRateStored", "uint256"), ("totalSupply", "uint256"),
                    ("interestRateModel", "address")) + [
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "mint", "stateMutability": "nonpayable",
     "inputs": [{"name": "mintAmount", "type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "redeemUnderlying", "stateMutability": "nonpayable",
     "inputs": [{"name": "redeemAmount", "type": "uint256"}], "outputs": [{"type": "uint256"}]},
]
ERC20_ABI = _views(("symbol", "string"), ("decimals", "uint8")) + [
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "allowance", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"type": "function", "name": "allocateTo", "stateMutability": "nonpayable",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "value", "type": "uint256"}], "outputs": []},
]
COMPTROLLER_ABI = [{"type": "function", "name": "getAllMarkets", "stateMutability": "view", "inputs": [],
                    "outputs": [{"type": "address[]"}]}]
IRM_ABI = _views(("blocksPerYear", "uint256"))


@dataclass
class Market:
    chain_id: int
    vtoken: str
    asset: str
    decimals: int
    supply_apy: float      # percent
    borrow_apy: float      # percent
    utilization: float     # percent of supplied liquidity that is borrowed
    cash: float            # idle liquidity, in asset units (withdrawable now)
    total_supplied: float  # in asset units
    blocks_per_year: int   # measured from the chain
    model_blocks_per_year: int | None  # what the interest rate model assumes

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("supply_apy", "borrow_apy", "utilization"):
            d[k] = round(d[k], 4)
        for k in ("cash", "total_supplied"):
            d[k] = round(d[k], 2)
        return d


def apy_percent(rate_per_block: int, blocks_per_year: int) -> float:
    per_day = rate_per_block / 1e18 * blocks_per_year / 365
    return ((1 + per_day) ** 365 - 1) * 100


_blocks_per_year: dict[int, tuple[float, int]] = {}


def blocks_per_year(w3: Web3) -> int:
    """Measured over the last BLOCK_SAMPLE blocks, cached for an hour per chain."""
    chain_id = w3.eth.chain_id
    cached = _blocks_per_year.get(chain_id)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    head = w3.eth.block_number
    elapsed = w3.eth.get_block(head)["timestamp"] - w3.eth.get_block(head - BLOCK_SAMPLE)["timestamp"]
    bpy = int(365 * 86400 * BLOCK_SAMPLE / elapsed)
    _blocks_per_year[chain_id] = (time.time(), bpy)
    return bpy


def read_market(w3: Web3, vtoken: str) -> Market:
    v = w3.eth.contract(Web3.to_checksum_address(vtoken), abi=VTOKEN_ABI)
    f = v.functions
    underlying = w3.eth.contract(f.underlying().call(), abi=ERC20_ABI)
    decimals = underlying.functions.decimals().call()
    bpy = blocks_per_year(w3)
    try:
        model_bpy = w3.eth.contract(f.interestRateModel().call(), abi=IRM_ABI).functions.blocksPerYear().call()
    except Exception:
        model_bpy = None
    cash, borrows, reserves = f.getCash().call(), f.totalBorrows().call(), f.totalReserves().call()
    supplied_raw = f.totalSupply().call() * f.exchangeRateStored().call() // 10**18
    denom = cash + borrows - reserves
    scale = 10**decimals
    return Market(
        chain_id=w3.eth.chain_id,
        vtoken=v.address,
        asset=underlying.functions.symbol().call(),
        decimals=decimals,
        supply_apy=apy_percent(f.supplyRatePerBlock().call(), bpy),
        borrow_apy=apy_percent(f.borrowRatePerBlock().call(), bpy),
        utilization=(borrows / denom * 100) if denom > 0 else 0.0,
        cash=cash / scale,
        total_supplied=supplied_raw / scale,
        blocks_per_year=bpy,
        model_blocks_per_year=model_bpy,
    )


_stable_vtokens: list[str] = []
_mainnet_cache: tuple[float, int, list[Market]] = (0.0, 0, [])


def _discover_mainnet_stables() -> list[str]:
    w3 = chain.mainnet()
    markets = w3.eth.contract(MAINNET_COMPTROLLER, abi=COMPTROLLER_ABI).functions.getAllMarkets().call()

    def symbol(addr):
        try:
            return addr, w3.eth.contract(addr, abi=VTOKEN_ABI).functions.symbol().call()
        except Exception:
            return addr, ""

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [addr for addr, sym in pool.map(symbol, markets) if sym in STABLE_VTOKENS]


def mainnet_stable_markets() -> tuple[int, list[Market]]:
    """(block number, markets) for every Venus Core Pool stablecoin market on BNB Chain, cached 5 min."""
    global _stable_vtokens, _mainnet_cache
    fetched_at, block, markets = _mainnet_cache
    if markets and time.time() - fetched_at < CACHE_SECONDS:
        return block, markets
    if not _stable_vtokens:
        _stable_vtokens = _discover_mainnet_stables()
    w3 = chain.mainnet()
    block = w3.eth.block_number

    def read_with_retry(addr):
        for attempt in range(3):
            try:
                return read_market(w3, addr)
            except Exception:
                if attempt == 2:
                    return None  # public RPCs throttle bursts; one missing market beats no report
                time.sleep(1 + attempt)

    with ThreadPoolExecutor(max_workers=4) as pool:
        markets = [m for m in pool.map(read_with_retry, _stable_vtokens) if m is not None]
    markets.sort(key=lambda m: m.supply_apy, reverse=True)
    _mainnet_cache = (time.time(), block, markets)
    return block, markets


# ---------------------------------------------------------------- testnet treasury


def token(symbol: str):
    return chain.testnet().eth.contract(Web3.to_checksum_address(TESTNET_TREASURY[symbol]["token"]), abi=ERC20_ABI)


def vtoken(symbol: str):
    return chain.testnet().eth.contract(Web3.to_checksum_address(TESTNET_TREASURY[symbol]["vtoken"]), abi=VTOKEN_ABI)


def treasury_balances(owner: str) -> dict[str, dict]:
    """Per asset: idle wallet balance and amount supplied to Venus, in raw units."""
    out = {}
    for sym in TESTNET_TREASURY:
        v = vtoken(sym)
        supplied = v.functions.balanceOf(owner).call() * v.functions.exchangeRateStored().call() // 10**18
        out[sym] = {"idle": token(sym).functions.balanceOf(owner).call(), "supplied": supplied,
                    "decimals": token(sym).functions.decimals().call()}
    return out


def supply(symbol: str, amount: int) -> str:
    owner = chain.agent_account().address
    t, v = token(symbol), vtoken(symbol)
    if t.functions.allowance(owner, v.address).call() < amount:
        chain.send(t.functions.approve(v.address, 2**256 - 1))
    # Compound-style vTokens return an error code instead of reverting: simulate first.
    code = v.functions.mint(amount).call({"from": owner})
    if code != 0:
        raise RuntimeError(f"Venus mint({symbol}) would fail with error code {code}")
    return chain.send(v.functions.mint(amount))


def redeem(symbol: str, amount: int) -> str:
    owner = chain.agent_account().address
    v = vtoken(symbol)
    code = v.functions.redeemUnderlying(amount).call({"from": owner})
    if code != 0:
        raise RuntimeError(f"Venus redeemUnderlying({symbol}) would fail with error code {code}")
    return chain.send(v.functions.redeemUnderlying(amount))


def faucet(symbol: str, amount: int) -> str:
    """Mint test tokens to the agent wallet (Venus testnet tokens expose allocateTo)."""
    return chain.send(token(symbol).functions.allocateTo(chain.agent_account().address, amount))
