# Halong Yield

A stablecoin **yield** agent on BNB Chain, built for BNB Chain's *Smart Money Era: Set and Earn*.

| | |
|---|---|
| ERC-8004 identity | **agent `2647`** on BSC testnet (chain **97**), IdentityRegistry `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Category | `yield` |
| Agent wallet | `0x6108C0076cF5997aCe0Ea0c92b7f90BC17243aba` |
| Agent card | https://halong-yield.vercel.app/.well-known/agent-card.json |
| Registration file | https://halong-yield.vercel.app/.well-known/agent-registration.json |
| Marketplaces | HelloFugu listing `26`; hireable over ERC-8183 by any escrow marketplace (Pokter, …) |

## What it does

**For hirers: a yield assessment.** Ask where a stablecoin amount earns the most, for example
*"Where should I deposit 5,000 USDT for the best yield over 90 days?"*. The agent reads every
Venus Core Pool stablecoin market on BNB Chain **mainnet** (supply APY, utilization, withdrawable
liquidity), ranks them for your amount and horizon, checks whether swapping into a better-paying
stablecoin covers its cost, and returns a JSON report: answer, recommendation, expected earnings,
per-market risk flags, assumptions and the block it was read at. Give a wallet address and it
also lists that wallet's current Venus stablecoin positions. Read-only: it never asks a hirer for
an approval or touches their funds.

**On its own: a treasury that acts on that analysis.** Twice a day (01:17 and 13:17 UTC) the
agent's wallet rebalances its own stablecoin treasury on Venus (BSC testnet):

1. rank USDT / USDC / BUSD by **mainnet** supply APY (testnet Venus has no borrowers, so its own
   APYs are ~0 and carry no signal), skipping markets above 92% utilization or with under
   1M withdrawable;
2. top up the leader's working float from the Venus testnet faucet if it runs low;
3. supply 40% of the leader's idle float to its Venus market;
4. redeem half of any supplied position the leader beats by more than 0.25 pp.

Each run's moves and transactions are in the `treasury cycle` workflow logs; current positions are
at `GET /` and in every assessment (`agent_treasury`), with a link to the wallet's activity.

APYs use the Venus UI convention (daily compounding of `supplyRatePerBlock`), with blocks per
year **measured from recent BNB Chain blocks**: interest accrues per block, and some interest
rate models still assume the pre-upgrade block time.

## How to hire it

- **ERC-8183 escrow (BSC testnet).** A2A `message/send` to the card's URL:
  `{"skill": "negotiate", "task_description": "...", "terms": {...}}` returns a quote signed by the
  agent wallet (0.05 U). Fund the job on AgenticCommerce `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE`
  with the returned envelope as the description (bnbagent's `build_job_description`), then send
  `{"skill": "notify_funded", "job_id": N, "chain_id": 97}`: the agent delivers before it answers.
  Funded jobs nobody announced are picked up by the 10-minute poll. The agent pins the report's
  manifest to IPFS, submits its hash with the gateway URL as `deliverable_url`, and settles the job
  after the dispute window so it ends `COMPLETED`.
- **HelloFugu.** Subscribe to listing 26 (tBNB, $0.01 per 10 minutes). `/hellofugu/<subId>` serves a
  live yield report for the renter's wallet; when the subscription ends the agent claims it.
- **Free preview.** `{"skill": "yield_scan"}` or a plain text message returns today's top Venus
  stablecoin APYs.

## Layout

```
agent/
  server.py     A2A card + JSON-RPC, ERC-8004 registration file, HelloFugu reports (stateless)
  worker.py     scheduled work: `tick` (deliver, settle, claim) and `cycle` (treasury)
  provider.py   ERC-8183 seller: signed quotes, delivery, settlement (bnbagent SDK)
  report.py     the yield assessment and the free preview
  venus.py      Venus market reads (mainnet + testnet) and supply / redeem
  strategy.py   the treasury cycle
  fugu.py       HelloFugu subscriptions: per-renter reports, claim on expiry
  chain.py      web3 connections; one locked, nonce-managed sender for the agent wallet
api/index.py    Vercel entrypoint
.github/workflows/
  tick.yml      every 10 minutes: python -m agent.worker tick
  cycle.yml     01:17 and 13:17 UTC: python -m agent.worker cycle
scripts/
  new_agent_wallet.py   create the agent wallet (key goes to .env)
  register_agent.py     ERC-8004 register + bind agent wallet + HelloFugu listing (campaign wallet)
hire/                   the owner's own Hire-track tooling (HelloFugu hires, status checks)
```

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cp .env.example .env            # fill PRIVATE_KEY (campaign wallet) for the scripts
python scripts/new_agent_wallet.py
python scripts/register_agent.py --base-url https://<your-host> --hellofugu
uvicorn agent.server:app --port 8000      # the A2A surface
python -m agent.worker tick               # one poll: deliver, settle, claim
python -m agent.worker cycle              # one treasury cycle
```

## Deployment

- **Vercel** serves the stateless A2A surface (`api/index.py`, `vercel.json`, 60 s functions).
  Environment: `AGENT_PRIVATE_KEY`, `STORAGE_API_KEY`, `AGENT_ID=2647`, `PUBLIC_BASE_URL`.
- **GitHub Actions** runs the scheduled work. Secrets: `AGENT_PRIVATE_KEY`, `STORAGE_API_KEY`;
  optional variables `PUBLIC_BASE_URL`, `STORAGE_GATEWAY_URL`. Both workflows share one
  concurrency group, so only one run signs from the agent wallet at a time.
- **Pinata** pins deliverables to IPFS (`STORAGE_API_KEY` is a Pinata JWT); buyers fetch them
  through the HTTPS gateway.

The campaign wallet's key is used only by the local scripts and never leaves the owner's machine.
