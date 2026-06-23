"""The hero: Conviction Ledger + self-briefing.

VesperClaw's differentiator is **accountability**: it doesn't just show the trades
it took — it shows the trades it *refused* and proves whether each refusal was
right (the market later hit the stop it avoided = a "good block").

This module:
  * build_ledger()   — aggregates Taken vs Refused, scoring refusals against what
                       actually happened (good_block / bad_block, adverse move avoided).
  * write_briefing() — an LLM-written, plain-English account: what it did, what it
                       refused, and what it learned. Falls back to a template.

Both read the existing audit trail (orders / vault_saves / mandates), so there is
no new source of truth — just an honest reckoning of it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

import config
from vesperclaw import store
from vesperclaw.llm_client import get_client


def build_ledger() -> dict[str, Any]:
    """Aggregate the Taken vs Refused conviction ledger from the audit trail."""
    orders = store.read_json(config.ORDERS_FILE, [])
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    orders = orders if isinstance(orders, list) else []
    saves = saves if isinstance(saves, list) else []

    # Taken
    taken_n = len(orders)
    wins = sum(1 for o in orders if o.get("win"))
    taken_pnl = round(sum(float(o.get("pnl", 0)) for o in orders), 2)
    win_rate = round(wins / taken_n * 100, 1) if taken_n else 0.0

    # Refused (resolved vault saves)
    resolved = [s for s in saves if s.get("verdict")]
    good = [s for s in resolved if s["verdict"] == "good_block"]
    bad = [s for s in resolved if s["verdict"] == "bad_block"]
    # adverse move VesperClaw avoided by refusing (good blocks = losses dodged)
    avoided = [abs(float(s.get("would_be_pnl_pct", 0))) for s in good]
    avg_avoided = round(sum(avoided) / len(avoided), 2) if avoided else 0.0
    refusal_accuracy = round(len(good) / len(resolved) * 100, 1) if resolved else 0.0

    return {
        "taken": {"count": taken_n, "wins": wins, "win_rate": win_rate, "pnl": taken_pnl},
        "refused": {
            "count": len(saves),
            "resolved": len(resolved),
            "good_blocks": len(good),
            "bad_blocks": len(bad),
            "refusal_accuracy_pct": refusal_accuracy,
            "avg_adverse_move_avoided_pct": avg_avoided,
        },
        "headline": (
            f"{len(good)}/{len(resolved)} refusals were correct"
            + (f" — avg {avg_avoided}% adverse move avoided" if avg_avoided else "")
            if resolved else "No refusals resolved yet"
        ),
    }


BRIEF_SYS = (
    "You are VesperClaw, an autonomous paper-trading agent writing a short, honest "
    "self-briefing for the humans watching you. Be concise (4-6 sentences), specific, "
    "and accountable: state what you traded, what you REFUSED and whether refusing was "
    "right, and one thing you'd do better. No hype, no disclaimers — just an analyst's "
    "candid debrief."
)


def write_briefing() -> dict[str, Any]:
    """Generate and persist the agent's plain-English accountability briefing."""
    ledger = build_ledger()
    orders = store.read_json(config.ORDERS_FILE, [])
    mandates = store.read_json(config.MANDATES_FILE, [])
    portfolio = store.read_json(config.PORTFOLIO_FILE, {})
    orders = orders[-8:] if isinstance(orders, list) else []
    recent_refusals = [
        m for m in (mandates if isinstance(mandates, list) else [])
        if m.get("vault", {}).get("decision") == "REJECTED" and m.get("action") != "NO_TRADE"
    ][-5:]

    equity = portfolio.get("equity", config.INITIAL_BALANCE)
    ret = (equity / config.INITIAL_BALANCE - 1) * 100

    client = get_client()
    fallback_text = (
        f"Equity {equity:.0f} ({ret:+.2f}%). Took {ledger['taken']['count']} trades "
        f"({ledger['taken']['win_rate']}% win rate). {ledger['headline']}. "
        f"Risk discipline held: every refusal is logged and scored against the outcome."
    )
    user = (
        f"Portfolio: equity {equity:.0f} ({ret:+.2f}%).\n"
        f"Conviction ledger: {ledger}\n"
        f"Recent closed trades: {[{k: o.get(k) for k in ('symbol','direction','pnl','reason','regime')} for o in orders]}\n"
        f"Recent REFUSED trades: {[{'symbol': m.get('symbol'), 'reason': m.get('vault',{}).get('reason')} for m in recent_refusals]}\n"
        "Write your self-briefing."
    )
    text = client.chat(BRIEF_SYS, user, fast=False, max_tokens=320) if client.available else fallback_text
    if not text or not text.strip():
        text = fallback_text

    brief = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "equity": equity,
        "return_pct": round(ret, 2),
        "ledger": ledger,
        "text": text.strip(),
    }
    store.write_json(config.BRIEFING_FILE, brief)
    logger.info(f"Briefing written: {ledger['headline']}")
    return brief


if __name__ == "__main__":
    import json
    print(json.dumps(write_briefing(), indent=2, default=str))
