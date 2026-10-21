"""Scheduled work, run by GitHub Actions (or by hand):

    python -m agent.worker tick                         deliver funded jobs, settle, claim ended HelloFugu subs
    python -m agent.worker cycle [--receipts PATH]      one treasury cycle; append its signed receipt to PATH
    python -m agent.worker snapshot --history PATH      append a mainnet APY snapshot to PATH

The workflows check out the repo's `data` branch, pass its files as PATH and commit the result.
Exits non-zero if a step failed, so a failing run shows up red in Actions.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import fugu, history, markets, receipts, settings, strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("halong.worker")


async def tick() -> dict:
    from .provider import Provider

    provider = Provider()
    return {
        "delivered": await provider.deliver_pending(),
        "settled": await provider.settle_due(),
        "claimed": await asyncio.to_thread(fugu.claim_ended),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.worker")
    parser.add_argument("task", choices=["tick", "cycle", "snapshot"])
    parser.add_argument("--receipts", type=Path, help="receipts.json to append the cycle's receipt to")
    parser.add_argument("--history", type=Path, help="history.json to append the snapshot to")
    args = parser.parse_args()

    if args.task == "snapshot":
        if not args.history:
            parser.error("snapshot needs --history")
        block, ms = markets.stable_markets()
        print(f"history.json now holds {history.append(args.history, history.snapshot(block, ms))} snapshots "
              f"(block {block}, {len(ms)} markets)")
        return 0

    if not settings.AGENT_PRIVATE_KEY:
        log.warning("AGENT_PRIVATE_KEY is not configured; nothing to do")
        return 0
    if args.task == "tick":
        result = asyncio.run(tick())
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 1 if any(d["status"] == "failed" for d in result["delivered"]) else 0

    out = strategy.run_cycle()
    if args.receipts:
        receipts.append_index(args.receipts, out["receipt"], out["ipfs"])
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
