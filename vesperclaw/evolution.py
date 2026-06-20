"""Mandate Evolution Engine — close-based, per-regime strategy learning.

Weights are learned PER REGIME: a strategy earns influence in the regime where it
actually makes money. Updates fire only when a trade closes (take-profit, stop-loss,
or timeout) — never on unrealized fluctuation — with guardrails so noise can't
whipsaw the system:

    * needs EVO_MIN_SAMPLES closed trades for a (regime, agent) before adjusting
    * each adjustment is capped at EVO_STEP_CAP
    * no agent ever drops below EVO_WEIGHT_FLOOR
    * the regime's weights are renormalised to sum to 1 after each change

It also reconciles **Vault Saves**: for trades the firewall blocked, it later checks
whether price would have hit the stop (good block) or the target (bad block).
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from loguru import logger

import config
from vesperclaw import store

STRATEGY_AGENTS = ["trend_agent", "mean_reversion_agent"]


def _default_store() -> dict[str, Any]:
    weights = {regime: dict(config.DEFAULT_WEIGHTS) for regime in config.REGIMES}
    stats = {
        regime: {a: {"samples": 0, "wins": 0, "pnl": 0.0} for a in config.DEFAULT_WEIGHTS}
        for regime in config.REGIMES
    }
    return {"weights": weights, "stats": stats}


def load() -> dict[str, Any]:
    data = store.read_json(config.WEIGHTS_FILE, None)
    if not data:
        data = _default_store()
        store.write_json(config.WEIGHTS_FILE, data)
        return data
    # backfill any missing regimes/agents
    base = _default_store()
    for regime in config.REGIMES:
        data.setdefault("weights", {}).setdefault(regime, dict(config.DEFAULT_WEIGHTS))
        data.setdefault("stats", {}).setdefault(regime, base["stats"][regime])
    return data


def weights_for(regime: str) -> dict[str, float]:
    """Return the learned weight row for a regime (for run_council)."""
    return load()["weights"].get(regime, dict(config.DEFAULT_WEIGHTS))


def _renormalize(row: dict[str, float]) -> dict[str, float]:
    """Clamp to floor, then scale so the row sums to 1.0."""
    clamped = {k: max(config.EVO_WEIGHT_FLOOR, v) for k, v in row.items()}
    total = sum(clamped.values())
    return {k: round(v / total, 4) for k, v in clamped.items()}


def update_from_close(trade: dict[str, Any]) -> dict[str, Any] | None:
    """Record a closed trade and adjust the responsible agent's regime weight.

    Returns an evolution-log entry if a weight changed, else None.
    """
    agent = trade.get("leading_agent")
    regime = trade.get("regime", "uncertain")
    if agent not in config.DEFAULT_WEIGHTS:
        return None

    data = load()
    stats = data["stats"].setdefault(regime, {})
    s = stats.setdefault(agent, {"samples": 0, "wins": 0, "pnl": 0.0})
    s["samples"] += 1
    s["wins"] += int(bool(trade.get("win")))
    s["pnl"] += float(trade.get("pnl_pct", 0.0))

    entry = None
    if s["samples"] >= config.EVO_MIN_SAMPLES and s["samples"] % config.EVO_MIN_SAMPLES == 0:
        row = dict(data["weights"][regime])
        old = row.get(agent, config.DEFAULT_WEIGHTS.get(agent, 0.25))
        win_rate = s["wins"] / s["samples"]
        # delta proportional to edge over a coin flip, bounded by the step cap
        delta = max(-config.EVO_STEP_CAP, min(config.EVO_STEP_CAP,
                                              (win_rate - 0.5) * 2 * config.EVO_STEP_CAP))
        row[agent] = old + delta
        new_row = _renormalize(row)
        data["weights"][regime] = new_row

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            "agent": agent,
            "samples": s["samples"],
            "win_rate": round(win_rate, 3),
            "avg_pnl_pct": round(s["pnl"] / s["samples"], 3),
            "old_weight": round(old, 4),
            "new_weight": new_row[agent],
            "reason": (
                f"{agent} won {win_rate:.0%} of its last {s['samples']} trades in "
                f"{regime}; weight {'raised' if delta >= 0 else 'cut'} "
                f"{old:.3f}->{new_row[agent]:.3f}."
            ),
        }
        store.append_json_list(config.EVOLUTION_FILE, entry, cap=500)
        logger.info(f"EVOLUTION {entry['reason']}")

    store.write_json(config.WEIGHTS_FILE, data)
    return entry


def reconcile_vault_saves(price: float) -> int:
    """Resolve pending Vault Saves against the current price.

    A blocked trade is a 'good_block' if price has moved against its intended
    direction past the stop, 'bad_block' if it reached the target. Returns the
    number newly resolved.
    """
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    if not isinstance(saves, list):
        return 0
    resolved = 0
    for sv in saves:
        if sv.get("resolved"):
            continue
        direction = sv.get("direction", "").lower()
        entry = sv.get("entry_price")
        sl, tp = sv.get("stop_loss"), sv.get("take_profit")
        if entry is None or direction not in ("long", "short"):
            continue
        verdict = None
        if direction == "long":
            if sl is not None and price <= sl:
                verdict = "good_block"
            elif tp is not None and price >= tp:
                verdict = "bad_block"
            wb_pnl = (price - entry) / entry * 100
        else:
            if sl is not None and price >= sl:
                verdict = "good_block"
            elif tp is not None and price <= tp:
                verdict = "bad_block"
            wb_pnl = (entry - price) / entry * 100
        if verdict:
            sv["resolved"] = True
            sv["verdict"] = verdict
            sv["would_be_pnl_pct"] = round(wb_pnl, 3)
            resolved += 1
    if resolved:
        store.write_json(config.VAULT_SAVES_FILE, saves)
    return resolved


def summary() -> dict[str, Any]:
    """Compact view for the dashboard."""
    data = load()
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    good = sum(1 for s in saves if s.get("verdict") == "good_block")
    bad = sum(1 for s in saves if s.get("verdict") == "bad_block")
    return {
        "weights": data["weights"],
        "stats": data["stats"],
        "vault_saves_good": good,
        "vault_saves_bad": bad,
        "vault_saves_pending": sum(1 for s in saves if not s.get("resolved")),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2))
