"""Human-readable loop memory for VesperClaw.

The JSON files are the source of truth. This module turns them into a small
STATE-style markdown file that judges and humans can inspect quickly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import config
from vesperclaw import briefing, store


def _money(value: Any, default: float = 0.0) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return f"${default:,.2f}"


def _pct(value: Any, default: float = 0.0) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return f"{default:+.2f}%"


def _guard_summary(portfolio: dict[str, Any]) -> dict[str, Any]:
    equity = float(portfolio.get("equity", config.INITIAL_BALANCE))
    peak = float(portfolio.get("peak_equity", max(equity, config.INITIAL_BALANCE)))
    day_start = float(portfolio.get("day_start_equity", config.INITIAL_BALANCE))
    cycle = int(portfolio.get("cycle", 0))
    guard_until = int(portfolio.get("profit_guard_until_cycle", 0))
    loss_streak = int(portfolio.get("consecutive_losses", 0))
    drawdown = (peak - equity) / peak if peak else 0.0
    daily_loss = (day_start - equity) / day_start if day_start else 0.0
    lockout = cycle < guard_until
    active = (
        config.PROFIT_GUARD_ENABLED
        and (
            lockout
            or loss_streak >= config.PROFIT_GUARD_LOSS_STREAK
            or drawdown >= config.PROFIT_GUARD_DRAWDOWN_PCT
            or daily_loss >= config.PROFIT_GUARD_DAILY_LOSS_PCT
        )
    )
    reasons = []
    if lockout:
        reasons.append(f"lockout until cycle {guard_until}")
    if loss_streak >= config.PROFIT_GUARD_LOSS_STREAK:
        reasons.append(f"{loss_streak} consecutive losses")
    if drawdown >= config.PROFIT_GUARD_DRAWDOWN_PCT:
        reasons.append(f"{drawdown:.2%} drawdown")
    if daily_loss >= config.PROFIT_GUARD_DAILY_LOSS_PCT:
        reasons.append(f"{daily_loss:.2%} daily loss")
    return {
        "active": active,
        "lockout": lockout,
        "reason": "; ".join(reasons) if reasons else "clear",
    }


def _latest(items: Any) -> dict[str, Any]:
    return items[-1] if isinstance(items, list) and items else {}


def build_loop_state() -> str:
    portfolio = store.read_json(config.PORTFOLIO_FILE, {})
    mandates = store.read_json(config.MANDATES_FILE, [])
    orders = store.read_json(config.ORDERS_FILE, [])
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    evo = store.read_json(config.EVOLUTION_FILE, [])
    profile = store.read_json(config.PROFILE_FILE, {})
    ledger = briefing.build_ledger()

    mandates = mandates if isinstance(mandates, list) else []
    orders = orders if isinstance(orders, list) else []
    saves = saves if isinstance(saves, list) else []
    evo = evo if isinstance(evo, list) else []
    latest = _latest(mandates)
    latest_order = _latest(orders)
    latest_evo = _latest(evo)
    guard = _guard_summary(portfolio)

    equity = float(portfolio.get("equity", config.INITIAL_BALANCE))
    ret = (equity / config.INITIAL_BALANCE - 1) * 100
    closed = int(portfolio.get("closed_trades", len(orders)))
    wins = int(portfolio.get("wins", 0))
    win_rate = wins / closed * 100 if closed else 0.0
    open_positions = portfolio.get("open_positions", [])
    open_positions = open_positions if isinstance(open_positions, list) else []
    profile_source = profile.get("source", "") if isinstance(profile, dict) else ""

    lines = [
        "# VesperClaw Loop State",
        "",
        f"Updated: {datetime.now(timezone.utc).isoformat()}",
        f"Cycle: {portfolio.get('cycle', 0)}",
        f"Equity: {_money(equity)} ({_pct(ret)})",
        f"Closed trades: {closed} | Win rate: {win_rate:.1f}% | Open positions: {len(open_positions)}",
        f"Profit Guard: {'LOCKOUT' if guard['lockout'] else 'ACTIVE' if guard['active'] else 'CLEAR'} - {guard['reason']}",
        "",
        "## Loop Map",
        "",
        "| Stage | Loop role | Current evidence |",
        "|---|---|---|",
        f"| 1. Perceive | Market Snapshot reads price, regime, funding, sentiment, and news. | Latest symbol: `{latest.get('symbol', 'n/a')}` / regime: `{latest.get('regime', 'n/a')}` |",
        f"| 2. Propose | Qwen Analyst Council acts as maker and writes thesis/counterargument. | Latest action: `{latest.get('action', 'n/a')}` / confidence: `{latest.get('confidence', 'n/a')}` |",
        f"| 3. Verify | AgentVault acts as checker and approves, downsizes, delays, or rejects. | Latest vault: `{latest.get('vault', {}).get('decision', 'n/a')}` |",
        f"| 4. Execute | PaperEngine simulates fills and writes the required CSV log. | Last close: `{latest_order.get('symbol', 'n/a')}` `{latest_order.get('direction', 'n/a')}` PnL `{latest_order.get('pnl', 'n/a')}` |",
        f"| 5. Monitor | Profit Guard and hard risk limits watch drawdown/loss streaks. | Guard: `{guard['reason']}` |",
        f"| 6. Learn | Evolution Engine updates weights from closed outcomes and Vault Saves. | Latest update: `{latest_evo.get('reason', 'none yet')}` |",
        "",
        "## Current Contract Command",
        "",
        profile_source or "No natural-language contract profile saved.",
        "",
        "## Accountability Memory",
        "",
        f"- Taken trades: {ledger['taken']['count']} with {ledger['taken']['win_rate']}% win rate and {_money(ledger['taken']['pnl'])} net PnL.",
        f"- Refused trades: {ledger['refused']['count']} logged, {ledger['refused']['resolved']} resolved.",
        f"- Refusal headline: {ledger['headline']}",
        f"- Vault saves file: `{config.VAULT_SAVES_FILE}`",
        f"- Mandate ledger: `{config.MANDATES_FILE}`",
        f"- Required trade log: `{config.TRADE_LOG_CSV}`",
        "",
        "## Latest Lesson",
        "",
    ]

    if guard["active"]:
        lines.append(
            "The loop is protecting capital: new trades must clear a higher confidence bar, "
            "choppy regimes are blocked, and sizing is capped until risk improves."
        )
    elif latest_order:
        result = "worked" if latest_order.get("win") else "failed"
        lines.append(
            f"The latest closed {latest_order.get('symbol')} {latest_order.get('direction')} trade {result} "
            f"via `{latest_order.get('reason')}` with PnL `{latest_order.get('pnl')}`."
        )
    else:
        lines.append("No closed trade lesson yet.")

    return "\n".join(lines) + "\n"


def write_loop_state() -> str:
    text = build_loop_state()
    store.ensure_dirs()
    with open(config.LOOP_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    return text


if __name__ == "__main__":
    print(write_loop_state())
