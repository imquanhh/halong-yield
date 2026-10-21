"""The protocol-neutral view of a lending market that every analysis works on."""

from dataclasses import asdict, dataclass


@dataclass
class Market:
    protocol: str            # "Venus" | "Aave V3"
    chain_id: int
    asset: str               # underlying symbol
    underlying: str          # underlying token address
    market: str              # vToken / aToken address
    decimals: int
    supply_apy: float        # percent
    borrow_apy: float        # percent
    utilization: float       # percent of supplied liquidity that is borrowed
    liquidity: float         # withdrawable right now, asset units
    total_supplied: float    # asset units
    price: float | None      # USD per token from the protocol's own oracle

    @property
    def key(self) -> str:
        return f"{self.protocol}:{self.asset}"

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("supply_apy", "borrow_apy", "utilization"):
            d[k] = round(d[k], 4)
        for k in ("liquidity", "total_supplied"):
            d[k] = round(d[k], 2)
        d["price"] = round(d["price"], 5) if d["price"] is not None else None
        return d
