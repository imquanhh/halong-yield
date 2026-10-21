"""Agent configuration, read once from the environment (.env locally; Vercel and GitHub Actions
environment variables / secrets in production)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AGENT_NAME = "Halong Yield"
CATEGORY = "yield"
VERSION = "1.0.0"
DESCRIPTION = (
    "Halong Yield is a stablecoin yield agent on BNB Chain (category: yield). Hire it for a yield "
    "assessment: it compares every dollar stablecoin market on Venus and Aave V3 (BNB Chain mainnet), "
    "ranks them by risk-adjusted APY with itemized penalties for utilization, exit liquidity, oracle "
    "depeg, volatility and issuer risk, filters by your risk tolerance, and returns expected earnings, "
    "an analyst note and, if you give a wallet, a step-by-step plan with the unsigned transactions. "
    "It runs its own Venus treasury on BSC testnet at weights set by that ranking and signs a "
    "verifiable receipt for every decision. Read-only for hirers: it never signs for you or moves "
    "your funds."
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


def _https(value: str) -> str:
    value = value.strip().rstrip("/")
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


# On Vercel the production domain comes from its system variable, so the card can never point
# elsewhere; PUBLIC_BASE_URL is for other hosts (and for GitHub Actions).
BASE_URL = _https(os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("PUBLIC_BASE_URL") or "http://localhost:8000")

AGENT_PRIVATE_KEY = (os.getenv("AGENT_PRIVATE_KEY") or "").strip()
WALLET_PASSWORD = os.getenv("WALLET_PASSWORD") or "halong-yield"  # SDK requires one; nothing is persisted

# 0.05 U per assessment. U on BSC testnet has 18 decimals.
JOB_PRICE_WEI = int(os.getenv("JOB_PRICE_WEI") or 5 * 10**16)

# Deliverables are pinned to IPFS (Pinata JWT) and served through the account's dedicated gateway,
# which serves only content that account pinned. Without a key (local development) they are
# written to DATA_DIR and served by the app itself.
STORAGE_API_KEY = os.getenv("STORAGE_API_KEY") or ""
STORAGE_GATEWAY_URL = _https(os.getenv("STORAGE_GATEWAY_URL") or "https://white-kind-mosquito-904.mypinata.cloud/ipfs")
if not STORAGE_GATEWAY_URL.endswith("/ipfs"):
    STORAGE_GATEWAY_URL += "/ipfs"

# Vercel Cron sends "Authorization: Bearer $CRON_SECRET"; without a secret the cron routes stay closed.
CRON_SECRET = os.getenv("CRON_SECRET") or ""
DATA_DIR = Path(os.getenv("DATA_DIR") or ("/tmp/halong" if os.getenv("VERCEL") else ROOT / ".agent-data"))


def require_agent_key() -> str:
    if not AGENT_PRIVATE_KEY:
        raise RuntimeError("AGENT_PRIVATE_KEY is not set (run scripts/new_agent_wallet.py first).")
    return AGENT_PRIVATE_KEY if AGENT_PRIVATE_KEY.startswith("0x") else "0x" + AGENT_PRIVATE_KEY
