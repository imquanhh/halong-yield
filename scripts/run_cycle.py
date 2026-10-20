"""Run one treasury cycle now, outside the server loop.

    python scripts/run_cycle.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import strategy  # noqa: E402

if __name__ == "__main__":
    block, ranked = strategy.rank_signal()
    print(f"mainnet block {block}: " + ", ".join(f"{r['asset']} {r['apy']:.2f}% ({r['reason']})" for r in ranked))
    for action in strategy.run_cycle():
        print(f"{action['kind']:<7} {action['detail']}\n        https://testnet.bscscan.com/tx/{action['tx']}")
    print(json.dumps(strategy.treasury_snapshot()["positions"], indent=1))
