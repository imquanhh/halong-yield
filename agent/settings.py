"""Agent configuration, read once from the environment (.env locally, Render env vars in prod)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AGENT_NAME = "Halong Yield"
CATEGORY = "yield"
VERSION = "1.0.0"
DESCRIPTION = (
    "Halong Yield is a stablecoin yield agent on BNB Chain (category: yield). "
    "Hire it for a yield assessment: it reads every Venus Core Pool stablecoin market on "
    "BNB Chain mainnet (supply APY, utilization, liquidity), ranks where a given amount earns "
    "the most for the risk, and returns a JSON report with expected earnings, risks and data "
    "sources. It also runs its own treasury on Venus (BSC testnet): every 12 hours it deposits "
    "idle stablecoins into the market its mainnet analysis ranks first and pulls funds back from "
    "markets that fall behind. Read-only for hirers: it never asks for approvals or moves a "
    "hirer's funds."
)

NETWORK = "bsc-testnet"
CHAIN_ID = 97
TESTNET_RPC = os.getenv("RPC_URL") or "https://bsc-testnet-rpc.publicnode.com"
# bnbagent reads RPC_URL too. Sharing the exact URL makes its NonceManager singleton (keyed by
# RPC URL + account) the same one chain.send uses, so SDK and treasury txs share one counter.
os.environ["RPC_URL"] = TESTNET_RPC
MAINNET_RPC = os.getenv("MAINNET_RPC_URL") or "https://bsc-rpc.publicnode.com"
EXPLORER = "https://testnet.bscscan.com"

IDENTITY_REGISTRY = "0x8004A818BFB912233c491871b3d84c89A494BD9e"
AGENT_ID = int(os.getenv("AGENT_ID") or 0) or None
BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")

AGENT_PRIVATE_KEY = (os.getenv("AGENT_PRIVATE_KEY") or "").strip()
# bnbagent's EVMWalletProvider keeps an encrypted keystore; this only protects that local file.
WALLET_PASSWORD = os.getenv("WALLET_PASSWORD") or "halong-yield-keystore"

# 0.05 U per assessment. U on BSC testnet has 18 decimals.
JOB_PRICE_WEI = int(os.getenv("JOB_PRICE_WEI") or 5 * 10**16)
CYCLE_HOURS = float(os.getenv("CYCLE_HOURS") or 12)
TICK_SECONDS = int(os.getenv("TICK_SECONDS") or 60)

DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT / ".agent-data")
STORAGE_API_KEY = os.getenv("STORAGE_API_KEY") or ""  # Pinata JWT; empty = serve deliverables from DATA_DIR


def require_agent_key() -> str:
    if not AGENT_PRIVATE_KEY:
        raise RuntimeError("AGENT_PRIVATE_KEY is not set (run scripts/new_agent_wallet.py first).")
    return AGENT_PRIVATE_KEY if AGENT_PRIVATE_KEY.startswith("0x") else "0x" + AGENT_PRIVATE_KEY
