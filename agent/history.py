"""APY history: snapshots the GitHub Actions tick appends to history.json on the repo's `data`
branch (public RPCs refuse historical state, so the agent keeps its own record).

The app reads it back over raw.githubusercontent.com; stats() turns it into a 7-day view per
market so the analysis does not chase a one-block spike.
"""

import json
import statistics
import time
from pathlib import Path

import requests

from .model import Market

HISTORY_URL = "https://raw.githubusercontent.com/imquanhh/halong-yield/data/history.json"
MAX_POINTS = 600
WINDOW_SECONDS = 7 * 86400
CACHE_SECONDS = 600

_cache: tuple[float, list[dict]] = (0.0, [])


def snapshot(block: int, markets: list[Market]) -> dict:
    return {
        "t": int(time.time()),
        "block": block,
        "apy": {m.key: round(m.supply_apy, 4) for m in markets},
        "util": {m.key: round(m.utilization, 2) for m in markets},
    }


def append(path: Path, point: dict) -> int:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "points": []}
    data["points"] = (data["points"] + [point])[-MAX_POINTS:]
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return len(data["points"])


def points() -> list[dict]:
    global _cache
    fetched_at, cached = _cache
    if time.time() - fetched_at < CACHE_SECONDS:
        return cached
    try:
        r = requests.get(HISTORY_URL, timeout=10)
        pts = r.json().get("points", []) if r.ok else []
    except (requests.RequestException, ValueError):
        pts = cached
    _cache = (time.time(), pts)
    return pts


def stats(key: str, now: float | None = None) -> dict | None:
    """7-day stats of one market's supply APY, or None with fewer than 3 snapshots."""
    cutoff = (now or time.time()) - WINDOW_SECONDS
    series = [p["apy"][key] for p in points() if p["t"] >= cutoff and key in p.get("apy", {})]
    if len(series) < 3:
        return None
    return {"n": len(series), "avg": statistics.fmean(series), "min": min(series), "max": max(series),
            "stdev": statistics.pstdev(series)}


def series(key: str, limit: int = 60) -> list[list[float]]:
    """[[unix time, apy], ...] for charts."""
    return [[p["t"], p["apy"][key]] for p in points() if key in p.get("apy", {})][-limit:]
