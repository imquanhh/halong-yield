"""Scheduled work, run by GitHub Actions (or by hand):

    python -m agent.worker tick    deliver funded jobs, settle past the dispute window, claim ended HelloFugu subs
    python -m agent.worker cycle   one treasury cycle on Venus

Exits non-zero if a step failed, so a failing run shows up red in Actions.
"""

import asyncio
import json
import logging
import sys

from . import fugu, settings, strategy

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
    task = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if not settings.AGENT_PRIVATE_KEY:
        log.warning("AGENT_PRIVATE_KEY is not configured; nothing to do")
        return 0
    if task == "tick":
        result = asyncio.run(tick())
        failed = [d for d in result["delivered"] if d["status"] in ("failed",)]
    elif task == "cycle":
        result = {"actions": strategy.run_cycle()}
        failed = []
    else:
        print(__doc__)
        return 2
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
