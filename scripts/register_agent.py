"""One-time setup from the campaign wallet (run locally; the campaign key never goes to the server).

    python scripts/register_agent.py --base-url https://halong-yield.vercel.app [--hellofugu]

1. tops up the agent wallet with tBNB for gas;
2. registers the agent on the ERC-8004 IdentityRegistry (BSC testnet), owned by the campaign
   wallet, with agentURI = <base-url>/.well-known/agent-registration.json; stores AGENT_ID in .env;
3. binds the agent's operating wallet (setAgentWallet, EIP-712 signed by that wallet);
4. with --hellofugu, lists the agent on HelloFugu as a yield agent.

Every step is skipped when already done, so it is safe to re-run.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import set_key
from eth_account import Account
from web3 import Web3
from web3.logs import DISCARD

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hire.config import campaign_account, connect, latest_timestamp, send_tx  # noqa: E402

REGISTRY = Web3.to_checksum_address("0x8004A818BFB912233c491871b3d84c89A494BD9e")
FUGU_REGISTRY = Web3.to_checksum_address("0xb2f36070E6eae3353E8e755172B477DF213ae248")
AGENT_GAS_TOPUP = Web3.to_wei(0.15, "ether")
FUGU_YIELD_CATEGORY = 2
FUGU_PRICE_USD8 = 1_000_000   # $0.01 per period
FUGU_PERIOD_SECONDS = 600     # 10 minutes

IDENTITY_ABI = [
    {"type": "function", "name": "register", "stateMutability": "nonpayable",
     "inputs": [{"name": "agentURI", "type": "string"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "ownerOf", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "tokenURI", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"type": "string"}]},
    {"type": "function", "name": "setAgentURI", "stateMutability": "nonpayable",
     "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "newURI", "type": "string"}], "outputs": []},
    {"type": "function", "name": "getAgentWallet", "stateMutability": "view",
     "inputs": [{"name": "agentId", "type": "uint256"}], "outputs": [{"type": "address"}]},
    {"type": "function", "name": "setAgentWallet", "stateMutability": "nonpayable",
     "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "newWallet", "type": "address"},
                {"name": "deadline", "type": "uint256"}, {"name": "signature", "type": "bytes"}], "outputs": []},
    {"type": "event", "name": "Transfer", "anonymous": False,
     "inputs": [{"name": "from", "type": "address", "indexed": True}, {"name": "to", "type": "address", "indexed": True},
                {"name": "tokenId", "type": "uint256", "indexed": True}]},
]
FUGU_ABI = [
    {"type": "function", "name": "listingByAgentId", "stateMutability": "view",
     "inputs": [{"name": "", "type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "list", "stateMutability": "nonpayable",
     "inputs": [{"name": "erc8004AgentId", "type": "uint256"}, {"name": "agentWallet", "type": "address"},
                {"name": "category", "type": "uint8"}, {"name": "priceUsd8PerPeriod", "type": "uint128"},
                {"name": "periodSeconds", "type": "uint32"}, {"name": "metadataURI", "type": "string"}],
     "outputs": [{"name": "listingId", "type": "uint256"}]},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="public HTTPS base URL of the deployed agent")
    parser.add_argument("--hellofugu", action="store_true", help="also list the agent on HelloFugu")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    agent_uri = f"{base}/.well-known/agent-registration.json"

    w3, owner = connect(), campaign_account()
    agent = Account.from_key(os.environ["AGENT_PRIVATE_KEY"])
    identity = w3.eth.contract(REGISTRY, abi=IDENTITY_ABI)
    print(f"campaign wallet {owner.address}\nagent wallet    {agent.address}\nagentURI        {agent_uri}")

    if w3.eth.get_balance(agent.address) < AGENT_GAS_TOPUP // 3:
        send_tx(w3, owner, {"to": agent.address, "value": AGENT_GAS_TOPUP})
        print(f"1. sent {Web3.from_wei(AGENT_GAS_TOPUP, 'ether')} tBNB to the agent wallet")
    else:
        print("1. agent wallet already has gas")

    agent_id = int(os.getenv("AGENT_ID") or 0)
    if agent_id:
        if identity.functions.ownerOf(agent_id).call() != owner.address:
            sys.exit(f"AGENT_ID {agent_id} is not owned by the campaign wallet")
        if identity.functions.tokenURI(agent_id).call() != agent_uri:
            send_tx(w3, owner, identity.functions.setAgentURI(agent_id, agent_uri).build_transaction({"from": owner.address}))
            print(f"2. agent {agent_id}: agentURI updated")
        else:
            print(f"2. agent {agent_id} already registered")
    else:
        if identity.functions.balanceOf(owner.address).call() > 0:
            sys.exit("The campaign wallet already owns an ERC-8004 agent. Put its id in .env as AGENT_ID "
                     "instead of registering a second one (duplicate IDs are excluded).")
        receipt = send_tx(w3, owner, identity.functions.register(agent_uri).build_transaction({"from": owner.address}))
        agent_id = identity.events.Transfer().process_receipt(receipt, errors=DISCARD)[0]["args"]["tokenId"]
        set_key(str(ROOT / ".env"), "AGENT_ID", str(agent_id), quote_mode="never")
        print(f"2. registered agent {agent_id} (tx {receipt['transactionHash'].to_0x_hex()}), AGENT_ID saved to .env")

    if identity.functions.getAgentWallet(agent_id).call() != agent.address:
        deadline = latest_timestamp(w3) + 240  # contract caps the deadline at 5 minutes ahead
        signed = Account.sign_typed_data(
            agent.key,
            domain_data={"name": "ERC8004IdentityRegistry", "version": "1", "chainId": 97, "verifyingContract": REGISTRY},
            message_types={"AgentWalletSet": [{"name": "agentId", "type": "uint256"}, {"name": "newWallet", "type": "address"},
                                              {"name": "owner", "type": "address"}, {"name": "deadline", "type": "uint256"}]},
            message_data={"agentId": agent_id, "newWallet": agent.address, "owner": owner.address, "deadline": deadline},
        )
        fn = identity.functions.setAgentWallet(agent_id, agent.address, deadline, signed.signature)
        fn.call({"from": owner.address})
        send_tx(w3, owner, fn.build_transaction({"from": owner.address}))
        print(f"3. agent wallet bound: {identity.functions.getAgentWallet(agent_id).call()}")
    else:
        print("3. agent wallet already bound")

    if args.hellofugu:
        fugu = w3.eth.contract(FUGU_REGISTRY, abi=FUGU_ABI)
        listing = fugu.functions.listingByAgentId(agent_id).call()
        if listing:
            print(f"4. already listed on HelloFugu (listing {listing})")
        else:
            fn = fugu.functions.list(agent_id, agent.address, FUGU_YIELD_CATEGORY, FUGU_PRICE_USD8,
                                     FUGU_PERIOD_SECONDS, agent_uri)
            fn.call({"from": owner.address})
            send_tx(w3, owner, fn.build_transaction({"from": owner.address}))
            print(f"4. listed on HelloFugu as listing {fugu.functions.listingByAgentId(agent_id).call()} "
                  f"(${FUGU_PRICE_USD8 / 1e8} per {FUGU_PERIOD_SECONDS // 60} min)")
    print(f"\nERC-8004 agent: 97:{agent_id}  ->  https://www.8004scan.io/agents/bsc-testnet/{agent_id}")


if __name__ == "__main__":
    main()
