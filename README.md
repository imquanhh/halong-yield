# Halong Yield

**Where your stablecoins earn the most on BNB Chain, after risk.** A stablecoin yield agent built for
BNB Chain's *Smart Money Era: Set and Earn*. Live dashboard: **https://halong-yield.vercel.app**

| | |
|---|---|
| ERC-8004 identity | **agent `2647`** on BSC testnet (chain **97**), IdentityRegistry `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Category | `yield` |
| Agent wallet | `0x6108C0076cF5997aCe0Ea0c92b7f90BC17243aba` |
| Agent card | https://halong-yield.vercel.app/.well-known/agent-card.json |
| Registration file | https://halong-yield.vercel.app/.well-known/agent-registration.json |
| Marketplaces | [Pokter](https://pokter.xyz/hire/97/2647) (ERC-8183, 0.05 U) · [HelloFugu](https://app.hellofugu.xyz/agent/97:2647) listing `26` (tBNB) |

## What a hire gets you

Send a brief such as *"Safe yield for 20k USDC over 6 months, wallet 0x…"*. The agent:

1. **Reads the brief**: asset, amount, horizon, risk tolerance, need for instant liquidity, wallet.
   Rules first; on paid jobs Claude (`claude-opus-5-5`, structured output) refines it when an API key is set.
2. **Compares every dollar stablecoin market on Venus Core Pool and Aave V3** on BNB Chain mainnet:
   supply APY, utilization, withdrawable liquidity, and each protocol's own oracle price.
3. **Ranks them by risk-adjusted APY**: base APY (spot, blended with the 7-day average once history
   exists) minus itemized penalties, each with its reason:

   | Penalty | Rule |
   |---|---|
   | Volatility | half the 7-day standard deviation of the APY |
   | Utilization | 0.08 pp per point above 85% (exits wait on repayments) |
   | Exit liquidity | grows with the position's share of what is withdrawable; excluded below 2x cover |
   | Depeg | the oracle's distance from $1 above 0.2%; excluded beyond 2% |
   | Issuer | 0 for USDT/USDC; 0.25 / 0.6 / 1.5 pp for smaller, synthetic or deprecated dollars |

   Grades: A (under 0.3 pp of penalties), B (under 1 pp), C. Risk tolerance low / medium / high admits A / A-B / A-C.
4. **Answers** with the best market for the amount and horizon, and whether swapping into a better
   stablecoin pays for an assumed 0.05% swap cost over that horizon.
5. **Plans the wallet** when an address is given: idle balances and Venus/Aave positions, then moves that
   never put more than 2% of a market's withdrawable liquidity in it, with annual gain, gas cost,
   break-even days and the **unsigned transactions** (approve, supply, withdraw) for each step.
6. **Writes an analyst note** from the computed facts only (Claude on paid jobs, a template otherwise).

The deliverable is pinned to IPFS and its manifest hash submitted on ERC-8183; the agent settles the job
after the dispute window so it ends `COMPLETED`. It never signs for the hirer or moves their funds.

## A treasury that acts on its own ranking, with signed receipts

Twice a day the agent runs its stablecoin treasury on Venus (BSC testnet). Testnet Venus has no
borrowers, so mainnet supplies the signal:

1. for USDT / USDC / BUSD, take the best mainnet market's risk-adjusted APY and weight assets by it;
2. grow the deployed target 5% (simulated deposits minted from the Venus testnet faucet);
3. redeem what sits above its target weight, supply what sits below (2% tolerance, 400 tokens per step);
4. **sign a decision receipt** with the inputs (mainnet block, best market per asset), the weights, positions
   before and after, and every transaction. Pinned to IPFS and indexed in
   [`receipts.json`](https://github.com/imquanhh/halong-yield/blob/data/receipts.json) on the `data` branch.

Verify one yourself:

```python
import json, requests
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

r = requests.get("https://raw.githubusercontent.com/imquanhh/halong-yield/data/receipts.json").json()["receipts"][-1]
body = {k: v for k, v in r.items() if k not in ("proof", "ipfs")}
digest = Web3.keccak(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
assert digest.to_0x_hex() == r["proof"]["hash"]
print(Account.recover_message(encode_defunct(hexstr=r["proof"]["hash"]), signature=r["proof"]["signature"]))
# 0x6108C0076cF5997aCe0Ea0c92b7f90BC17243aba: the agent wallet bound to ERC-8004 agent 2647
```

The dashboard also checks each signature in the browser.

## How to hire it

- **ERC-8183 escrow (BSC testnet).** A2A `message/send` to the card's URL with
  `{"skill": "negotiate", "task_description": "..."}` returns a quote signed by the agent wallet (0.05 U).
  Fund the job on AgenticCommerce `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` with the returned envelope
  as description (bnbagent's `build_job_description`), then send
  `{"skill": "notify_funded", "job_id": N, "chain_id": 97}`: the agent delivers before it answers.
  Jobs nobody announced are picked up by the scheduled poll.
- **HelloFugu.** Subscribe to listing 26 (tBNB, $0.01 per 10 minutes). `/hellofugu/<subId>` serves a live
  report for the renter's wallet; when the subscription ends the agent claims it.
- **Free preview.** `{"skill": "yield_scan"}` or any plain text message.

## Layout

```
agent/
  server.py     dashboard, A2A card + JSON-RPC, ERC-8004 registration file, /api/overview (stateless)
  worker.py     scheduled work: tick (deliver, settle, claim), cycle (treasury), snapshot (APY history)
  report.py     the assessment (brief, ranking, answer, wallet plan, analyst note) and the free preview
  markets.py    Venus + Aave V3 stablecoin markets      venus.py, aave.py   protocol reads
  risk.py       risk-adjusted APY and grades            wallet.py           wallet plan and calldata
  strategy.py   the treasury cycle                      receipts.py         signed decision receipts
  history.py    APY snapshots on the data branch        llm.py              optional Claude layer
  provider.py   ERC-8183 seller (bnbagent SDK)          fugu.py             HelloFugu subscriptions
  chain.py      web3, one locked nonce-managed sender   web/index.html      the dashboard
api/index.py    Vercel entrypoint
.github/workflows/  tick.yml (every 10 min) and cycle.yml (01:17, 13:17 UTC), writing to the data branch
scripts/        new_agent_wallet.py; register_agent.py (ERC-8004, agent wallet, HelloFugu; campaign wallet)
hire/           the owner's own Hire-track tooling
```

## Run and deploy

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env
python scripts/new_agent_wallet.py
python scripts/register_agent.py --base-url https://<your-host> --hellofugu
uvicorn agent.server:app --port 8000                     # dashboard + A2A
python -m agent.worker tick | cycle | snapshot --history PATH
```

- **Vercel** serves the stateless app (60 s functions) and a daily cron backup (`/cron/cycle`,
  `/cron/tick`, guarded by `CRON_SECRET`). Env: `AGENT_PRIVATE_KEY`, `STORAGE_API_KEY`, `AGENT_ID`,
  `CRON_SECRET`, optional `ANTHROPIC_API_KEY`.
- **GitHub Actions** runs the schedule and commits APY snapshots and receipts to the `data` branch.
  Secrets: `AGENT_PRIVATE_KEY`, `STORAGE_API_KEY`, optional `ANTHROPIC_API_KEY`. Both workflows share one
  concurrency group, so only one run signs from the agent wallet at a time.
- **Pinata** pins deliverables and receipts to IPFS.

The campaign wallet's key is used only by the local scripts and never leaves the owner's machine.
