"""Optional Claude layer for paid assessments: reads a free-form brief into structured intent, and
writes a short analyst note from numbers the agent already computed.

Every figure in the note comes from the facts passed in; the model is told to add none of its
own. Without ANTHROPIC_API_KEY, or on any API error, refusal or timeout, callers get None and
fall back to the deterministic parser and the template note, so the agent never depends on it.
"""

import json
import logging
import os

log = logging.getLogger("halong.llm")

MODEL = "claude-opus-5-5"
ASSETS = ["USDT", "USDC", "FDUSD", "USD1", "DAI", "U", "USDe", "lisUSD", "TUSD", "BUSD"]

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "assets": {"type": "array", "items": {"type": "string", "enum": ASSETS},
                   "description": "Stablecoins the brief is about; empty if it names none."},
        "amount_usd": {"type": "number", "description": "Amount to place in USD; 0 if not stated."},
        "horizon_days": {"type": "integer", "description": "Holding period in days; 0 if not stated."},
        "risk_tolerance": {"type": "string", "enum": ["low", "medium", "high"]},
        "needs_instant_liquidity": {"type": "boolean"},
        "wallet": {"type": "string", "description": "0x address in the brief, or an empty string."},
    },
    "required": ["assets", "amount_usd", "horizon_days", "risk_tolerance", "needs_instant_liquidity", "wallet"],
    "additionalProperties": False,
}
NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "note": {"type": "string"},
        "watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "note", "watch"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = (
    "You turn a client's brief for a stablecoin yield assessment on BNB Chain into structured fields. "
    "Read only what the brief says or clearly implies. Risk tolerance is medium unless the brief signals "
    "otherwise (for example 'safe', 'conservative' mean low; 'max yield', 'aggressive' mean high). "
    "needs_instant_liquidity is true when the client says they may need the money back at any moment."
)
NOTE_SYSTEM = (
    "You are the analyst voice of Halong Yield, a stablecoin yield agent on BNB Chain. Write a note for the "
    "client from the facts JSON you are given. Use only numbers that appear in the facts; do not estimate, "
    "round differently or add any figure of your own. Headline: one sentence with the recommendation. "
    "Note: three to five plain sentences on why this market beats the alternatives once risk is counted, "
    "and what the client should do next. Watch: up to three specific things that would change the advice."
)


def _client():
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic(max_retries=1)


def _call(system: str, user: str, schema: dict, effort: str, timeout: float) -> dict | None:
    client = _client()
    if client is None:
        return None
    import anthropic
    try:
        response = client.with_options(timeout=timeout).beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APITimeoutError:
        log.warning("Claude timed out after %.0fs; using the deterministic path", timeout)
        return None
    except anthropic.RateLimitError:
        log.warning("Claude rate limited; using the deterministic path")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude returned %s; using the deterministic path", exc.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude unreachable; using the deterministic path")
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude stopped with %s; using the deterministic path", response.stop_reason)
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def parse_brief(task: str, timeout: float = 10.0) -> dict | None:
    return _call(BRIEF_SYSTEM, f"Brief:\n{task}", BRIEF_SCHEMA, effort="low", timeout=timeout)


def analyst_note(facts: dict, timeout: float = 15.0) -> dict | None:
    note = _call(NOTE_SYSTEM, "Facts:\n" + json.dumps(facts, ensure_ascii=False), NOTE_SCHEMA,
                 effort="low", timeout=timeout)
    return {**note, "by": MODEL} if note else None
