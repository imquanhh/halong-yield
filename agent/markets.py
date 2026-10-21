"""Every stablecoin lending market the agent compares on BNB Chain mainnet: Venus Core Pool
and Aave V3. A protocol that fails to answer is left out of that read, not fatal."""

import logging

from . import aave, venus
from .model import Market

log = logging.getLogger("halong.markets")


def stable_markets() -> tuple[int, list[Market]]:
    block, markets = venus.mainnet_stable_markets()
    try:
        markets = markets + aave.mainnet_stable_markets()
    except Exception:
        log.exception("Aave V3 read failed; continuing with Venus only")
    return block, sorted(markets, key=lambda m: m.supply_apy, reverse=True)
