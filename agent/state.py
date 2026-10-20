"""Small JSON state file in DATA_DIR: the action log, job bookkeeping and loop cursors.

The chain stays the source of truth; this file only saves work (and keeps the public
action log readable) across restarts. Losing it is harmless.
"""

import json
import threading
import time

from . import settings

_LOCK = threading.Lock()
_PATH = settings.DATA_DIR / "state.json"
_DEFAULT = {"actions": [], "submitted_jobs": {}, "last_cycle_at": 0, "fugu_sub_cursor": 0, "fugu_reports": {}}


def _read() -> dict:
    if not _PATH.exists():
        return json.loads(json.dumps(_DEFAULT))
    return {**_DEFAULT, **json.loads(_PATH.read_text(encoding="utf-8"))}


def _write(data: dict) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def get(key: str):
    with _LOCK:
        return _read()[key]


def update(fn) -> None:
    """fn(state_dict) mutates the state in place."""
    with _LOCK:
        data = _read()
        fn(data)
        _write(data)


def log_action(kind: str, detail: str, tx_hash: str | None) -> dict:
    entry = {"at": int(time.time()), "kind": kind, "detail": detail, "tx": tx_hash}
    update(lambda s: s["actions"].append(entry))
    return entry


def recent_actions(n: int = 10) -> list[dict]:
    return get("actions")[-n:][::-1]
