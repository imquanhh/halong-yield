"""Create the agent's operating wallet and store its key in .env (never printed).

    python scripts/new_agent_wallet.py
"""

import sys
from pathlib import Path

from dotenv import dotenv_values, set_key
from eth_account import Account

ENV = Path(__file__).resolve().parent.parent / ".env"


def main():
    if dotenv_values(ENV).get("AGENT_PRIVATE_KEY"):
        address = Account.from_key(dotenv_values(ENV)["AGENT_PRIVATE_KEY"]).address
        sys.exit(f"AGENT_PRIVATE_KEY already set in .env (agent wallet {address}). Not overwriting.")
    acct = Account.create()
    set_key(str(ENV), "AGENT_PRIVATE_KEY", acct.key.hex(), quote_mode="never")
    print(f"Agent wallet: {acct.address}")
    print("Key written to .env as AGENT_PRIVATE_KEY.")


if __name__ == "__main__":
    main()
