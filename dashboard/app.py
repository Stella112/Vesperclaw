"""VesperClaw dashboard - the glass-box trading terminal.

Reads the plain JSON/CSV audit trail the loop writes and renders the full
decision lifecycle: snapshot -> mandate -> vault -> paper fills -> equity ->
learning -> Vault Saves. The UI leads with accountability, not just PnL.

Run: streamlit run dashboard/app.py
"""
from __future__ import annotations

import os
import sys
import json
import hashlib
from html import escape
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Allow importing the package when launched via `streamlit run`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from vesperclaw import briefing as briefing_mod  # noqa: E402
from vesperclaw import agent_hub, evolution, loop_state, meme_radar, prediction, store, vibe  # noqa: E402

st.set_page_config(page_title="VesperClaw | Bitget Agent", page_icon=":chart_with_upwards_trend:", layout="wide")

REGIME_COLORS = {
    "trend_up": "#20e3b2",
    "trend_down": "#ff5470",
    "range": "#ffd166",
    "uncertain": "#8b98b8",
}
ACTION_COLORS = {"LONG": "#20e3b2", "SHORT": "#ff5470", "NO_TRADE": "#8b98b8"}
VAULT_COLORS = {
    "APPROVED": "#20e3b2",
    "APPROVED_DOWNSIZED": "#ffd166",
    "REJECTED": "#ff5470",
    "DELAYED": "#8b98b8",
}


def apply_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --vc-bg: #070b12;
            --vc-panel: rgba(13, 20, 35, 0.88);
            --vc-panel-2: rgba(19, 29, 49, 0.86);
            --vc-line: rgba(123, 220, 255, 0.18);
            --vc-text: #eef6ff;
            --vc-muted: #8b98b8;
            --vc-cyan: #22d3ee;
            --vc-green: #20e3b2;
            --vc-red: #ff5470;
            --vc-yellow: #ffd166;
        }

        .stApp {
            background:
                radial-gradient(circle at 14% 0%, rgba(34, 211, 238, 0.15), transparent 32%),
                radial-gradient(circle at 86% 12%, rgba(32, 227, 178, 0.11), transparent 34%),
                linear-gradient(180deg, #070b12 0%, #0a0f1c 46%, #06080f 100%);
            color: var(--vc-text);
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(7, 11, 18, 0.98), rgba(13, 20, 35, 0.96));
            border-right: 1px solid var(--vc-line);
        }

        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
            max-width: 1560px;
        }

        h1, h2, h3 {
            letter-spacing: 0;
        }

        div[data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(15, 23, 42, 0.94), rgba(8, 13, 24, 0.94));
            border: 1px solid var(--vc-line);
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.24);
        }

        div[data-testid="stMetricLabel"] p {
            color: var(--vc-muted);
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        div[data-testid="stMetricValue"] {
            color: var(--vc-text);
            font-size: 1.55rem;
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--vc-line);
            border-radius: 8px;
            overflow: hidden;
        }

        .vc-hero {
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(34, 211, 238, 0.22);
            border-radius: 8px;
            padding: 24px 26px;
            background:
                linear-gradient(135deg, rgba(10, 17, 32, 0.96), rgba(11, 28, 45, 0.86)),
                repeating-linear-gradient(90deg, rgba(255,255,255,0.03) 0 1px, transparent 1px 56px);
            box-shadow: 0 22px 70px rgba(0, 0, 0, 0.34);
        }

        .vc-hero:after {
            content: "";
            position: absolute;
            inset: auto -12% -72% 44%;
            height: 210px;
            transform: rotate(-8deg);
            background: linear-gradient(90deg, transparent, rgba(34, 211, 238, 0.18), transparent);
        }

        .vc-eyebrow {
            color: var(--vc-green);
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.18em;
            text-transform: uppercase;
        }

        .vc-title {
            margin: 6px 0 6px 0;
            color: var(--vc-text);
            font-size: clamp(2.1rem, 4vw, 4.2rem);
            font-weight: 800;
            line-height: 0.98;
        }

        .vc-subtitle {
            max-width: 980px;
            color: #b7c6e4;
            font-size: 1.02rem;
            line-height: 1.55;
        }

        .vc-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 18px;
        }

        .vc-chip, .vc-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border: 1px solid var(--vc-line);
            border-radius: 999px;
            padding: 6px 10px;
            background: rgba(255,255,255,0.04);
            color: #dce8ff;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.02em;
        }

        .vc-panel {
            border: 1px solid var(--vc-line);
            border-radius: 8px;
            padding: 16px;
            background: var(--vc-panel);
            box-shadow: 0 14px 45px rgba(0, 0, 0, 0.22);
            min-height: 100%;
        }

        .vc-panel h3 {
            margin: 0 0 8px 0;
            font-size: 0.95rem;
            color: #f4f8ff;
        }

        .vc-panel p {
            color: #aebcda;
            margin-bottom: 0;
        }

        .vc-ledger-stat {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin-top: 12px;
        }

        .vc-mini {
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            padding: 10px;
        }

        .vc-mini span {
            display: block;
            color: var(--vc-muted);
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.07em;
        }

        .vc-mini strong {
            color: var(--vc-text);
            display: block;
            margin-top: 3px;
            font-size: 1.15rem;
        }

        .vc-rule {
            height: 1px;
            background: linear-gradient(90deg, transparent, rgba(34, 211, 238, 0.42), transparent);
            margin: 18px 0;
        }

        .vc-caption {
            color: var(--vc-muted);
            font-size: 0.82rem;
        }

        .vc-stack {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
        }

        .vc-step-num {
            width: 28px;
            height: 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: rgba(34, 211, 238, 0.14);
            border: 1px solid rgba(34, 211, 238, 0.34);
            color: var(--vc-cyan);
            font-weight: 800;
            margin-right: 8px;
        }

        .vc-callout {
            border: 1px solid rgba(32, 227, 178, 0.22);
            border-radius: 8px;
            padding: 14px 16px;
            background: linear-gradient(135deg, rgba(32, 227, 178, 0.09), rgba(34, 211, 238, 0.05));
            color: #dce8ff;
        }

        .vc-verdict {
            border: 1px solid rgba(34, 211, 238, 0.20);
            border-left: 4px solid var(--verdict-color);
            border-radius: 8px;
            padding: 16px;
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.94), rgba(7, 11, 18, 0.92));
        }

        .vc-verdict-title {
            color: var(--vc-text);
            font-size: 1.35rem;
            font-weight: 850;
            margin-bottom: 4px;
        }

        .vc-list {
            margin: 8px 0 0 0;
            padding-left: 18px;
            color: #b7c6e4;
        }

        .vc-score-card {
            border: 1px solid rgba(32, 227, 178, 0.24);
            border-radius: 8px;
            padding: 18px;
            background:
                radial-gradient(circle at 50% 10%, rgba(32, 227, 178, 0.14), transparent 48%),
                rgba(9, 15, 27, 0.94);
            text-align: center;
            min-height: 100%;
        }

        .vc-score-ring {
            width: 220px;
            height: 220px;
            margin: 0 auto 10px auto;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background:
                conic-gradient(var(--vc-green) calc(var(--score) * 1%), rgba(255,255,255,0.08) 0),
                radial-gradient(circle, #09111f 58%, transparent 59%);
            box-shadow: 0 0 42px rgba(32, 227, 178, 0.14);
        }

        .vc-score-ring-inner {
            width: 164px;
            height: 164px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            background: #07101c;
            border: 1px solid rgba(255,255,255,0.08);
        }

        .vc-score-value {
            color: var(--vc-text);
            font-size: 3.6rem;
            line-height: 0.9;
            font-weight: 850;
        }

        .vc-score-label {
            color: var(--vc-green);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            margin-top: 8px;
        }

        .vc-brain {
            border: 1px solid var(--vc-line);
            border-radius: 8px;
            background: rgba(5, 9, 16, 0.72);
            padding: 10px 12px;
            max-height: 320px;
            overflow-y: auto;
            font-family: Consolas, "JetBrains Mono", monospace;
        }

        .vc-brain-line {
            border-bottom: 1px solid rgba(255,255,255,0.06);
            color: #dce8ff;
            font-size: 0.8rem;
            padding: 8px 0;
        }

        .vc-brain-line:last-child {
            border-bottom: 0;
        }

        .vc-brain-line span {
            color: var(--vc-cyan);
            margin-right: 8px;
        }

        @media (max-width: 900px) {
            .vc-stack {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def color_dot(color: str) -> str:
    return (
        f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
        f"background:{color};box-shadow:0 0 14px {color};'></span>"
    )


def badge(label: str, color: str) -> str:
    return f"<span class='vc-pill'>{color_dot(color)}{label}</span>"


def pct(value: Any, fallback: str = "n/a") -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return fallback


def safe_text(value: Any, fallback: str = "n/a") -> str:
    return escape(str(value if value not in (None, "") else fallback))


def data_file(name: str, fallback: str) -> str:
    return getattr(config, name, os.path.join(getattr(config, "DATA_DIR", "data"), fallback))


def pnl_summary(portfolio: dict) -> dict[str, float]:
    equity = float(portfolio.get("equity", config.INITIAL_BALANCE))
    balance = float(portfolio.get("balance", equity))
    day_start = float(portfolio.get("day_start_equity", config.INITIAL_BALANCE))
    return {
        "total": round(equity - config.INITIAL_BALANCE, 2),
        "day": round(equity - day_start, 2),
        "unrealized": round(equity - balance, 2),
    }


def profit_guard_summary(portfolio: dict) -> dict[str, Any]:
    equity = float(portfolio.get("equity", config.INITIAL_BALANCE))
    peak = float(portfolio.get("peak_equity", max(equity, config.INITIAL_BALANCE)))
    day_start = float(portfolio.get("day_start_equity", config.INITIAL_BALANCE))
    drawdown = (peak - equity) / peak if peak else 0.0
    daily_loss = (day_start - equity) / day_start if day_start else 0.0
    cycle = int(portfolio.get("cycle", 0))
    guard_until = int(portfolio.get("profit_guard_until_cycle", 0))
    loss_streak = int(portfolio.get("consecutive_losses", 0))
    hard_drawdown_lock = drawdown >= config.PROFIT_GUARD_HARD_LOCK_DRAWDOWN_PCT
    hard_daily_lock = daily_loss >= config.PROFIT_GUARD_HARD_LOCK_DAILY_LOSS_PCT
    lockout = cycle < guard_until or hard_drawdown_lock or hard_daily_lock
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
        if cycle < guard_until:
            reasons.append(f"lockout until cycle {guard_until}")
        if hard_drawdown_lock:
            reasons.append(f"hard drawdown brake {drawdown:.2%}")
        if hard_daily_lock:
            reasons.append(f"hard daily-loss brake {daily_loss:.2%}")
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
        "loss_streak": loss_streak,
        "guard_until": guard_until,
        "drawdown_pct": drawdown * 100,
        "daily_loss_pct": daily_loss * 100,
    }


def compact_usd(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    abs_num = abs(num)
    if abs_num >= 1_000_000_000:
        return f"${num / 1_000_000_000:.2f}B"
    if abs_num >= 1_000_000:
        return f"${num / 1_000_000:.2f}M"
    if abs_num >= 1_000:
        return f"${num / 1_000:.1f}K"
    return f"${num:,.2f}"


@st.cache_data(ttl=120, show_spinner=False)
def cached_meme_scan(query: str, guard_active: bool, guard_reason: str) -> dict[str, Any]:
    guard = {"active": guard_active, "reason": guard_reason}
    return meme_radar.analyze(query=query, guard=guard)


@st.cache_data(ttl=180, show_spinner=False)
def cached_world_cup_board(limit: int) -> list[dict[str, Any]]:
    return prediction.fetch_markets(limit, topic="world_cup")


def latest_vault_decision(mandates: list[dict]) -> str:
    for mandate in reversed(mandates):
        decision = mandate.get("vault", {}).get("decision")
        if decision:
            return decision
    return "IDLE"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def mandate_fingerprint(mandate: dict) -> str:
    payload = json.dumps(mandate, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def conviction_metrics(portfolio: dict, orders: list[dict], saves: list[dict]) -> dict[str, Any]:
    closed = len(orders)
    wins = sum(1 for order in orders if order.get("win"))
    win_rate = wins / closed * 100 if closed else 0.0

    resolved = [save for save in saves if save.get("verdict")]
    good = [save for save in resolved if save.get("verdict") == "good_block"]
    refusal_accuracy = len(good) / len(resolved) * 100 if resolved else 50.0

    equity = float(portfolio.get("equity", config.INITIAL_BALANCE))
    peak = float(portfolio.get("peak_equity", max(equity, config.INITIAL_BALANCE)))
    drawdown_pct = (equity - peak) / peak * 100 if peak else 0.0
    drawdown_discipline = clamp(100 - abs(drawdown_pct) * 5)

    evidence = clamp((closed + len(saves)) / 20 * 100)
    score = round(
        0.28 * win_rate
        + 0.30 * refusal_accuracy
        + 0.27 * drawdown_discipline
        + 0.15 * evidence
    )

    avoided_moves = [abs(float(save.get("would_be_pnl_pct", 0))) for save in good]
    avg_avoided = sum(avoided_moves) / len(avoided_moves) if avoided_moves else 0.0
    return {
        "score": int(clamp(score)),
        "win_rate": round(win_rate, 1),
        "refusal_accuracy": round(refusal_accuracy, 1),
        "drawdown_pct": round(drawdown_pct, 2),
        "evidence": round(evidence, 1),
        "good_blocks": len(good),
        "bad_blocks": sum(1 for save in resolved if save.get("verdict") == "bad_block"),
        "avg_avoided": round(avg_avoided, 2),
    }


def load_all() -> tuple[dict, list[dict], list[dict], list[dict], list[dict]]:
    portfolio = store.read_json(config.PORTFOLIO_FILE, {})
    mandates = store.read_json(config.MANDATES_FILE, [])
    orders = store.read_json(config.ORDERS_FILE, [])
    evo = store.read_json(config.EVOLUTION_FILE, [])
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    return portfolio, mandates, orders, evo, saves


def hero(portfolio: dict, mandates: list[dict]) -> None:
    latest = mandates[-1] if mandates else {}
    vault = latest.get("vault", {})
    last_action = latest.get("action", "AWAITING_SIGNAL")
    last_regime = latest.get("regime", "booting")
    eq = portfolio.get("equity", config.INITIAL_BALANCE)
    ret = (eq / config.INITIAL_BALANCE - 1) * 100
    pnl = pnl_summary(portfolio)
    last_seen = latest.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds")

    chips = [
        badge(f"{config.LLM_PROVIDER.upper()} reasoning", "#22d3ee"),
        badge(f"{len(config.SYMBOL_ALLOWLIST)}-asset Bitget scan", "#20e3b2"),
        badge(f"{config.LEVERAGE:g}x paper perps", "#ffd166"),
        badge(f"Vault {vault.get('decision', 'IDLE')}", VAULT_COLORS.get(vault.get("decision"), "#8b98b8")),
        badge(f"{last_action} / {last_regime}", ACTION_COLORS.get(last_action, "#8b98b8")),
    ]

    st.markdown(
        f"""
        <section class="vc-hero">
            <div class="vc-eyebrow">Bitget AI Base Camp S1 - Trading Agent</div>
            <div class="vc-title">VesperClaw</div>
            <div class="vc-subtitle">
                An accountable Bitget trading agent with receipts. It scans the basket,
                writes a thesis and counterargument, routes every mandate through AgentVault,
                scores restraint, fingerprints decisions, and evolves trust from closed outcomes.
            </div>
            <div class="vc-chip-row">{''.join(chips)}</div>
            <div class="vc-chip-row">
                <span class="vc-chip">Equity ${eq:,.2f}</span>
                <span class="vc-chip">Paper PnL ${pnl['total']:+,.2f}</span>
                <span class="vc-chip">Return {ret:+.2f}%</span>
                <span class="vc-chip">Last mandate {last_seen}</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def kpi_strip(portfolio: dict) -> None:
    eq = portfolio.get("equity", config.INITIAL_BALANCE)
    ret = (eq / config.INITIAL_BALANCE - 1) * 100
    pnl = pnl_summary(portfolio)
    closed = portfolio.get("closed_trades", 0)
    wr = (portfolio.get("wins", 0) / closed * 100) if closed else 0.0
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Equity", f"${eq:,.2f}", f"{ret:+.2f}%")
    c2.metric("Paper PnL", f"${pnl['total']:+,.2f}", f"Today ${pnl['day']:+,.2f}")
    c3.metric("Unrealized PnL", f"${pnl['unrealized']:+,.2f}")
    c4.metric("Closed trades", closed)
    c5.metric("Win rate", f"{wr:.1f}%")
    c6.metric("Cycle", portfolio.get("cycle", 0))


def judge_brief_panel(portfolio: dict, mandates: list[dict], orders: list[dict]) -> None:
    guard = profit_guard_summary(portfolio)
    pnl = pnl_summary(portfolio)
    latest = mandates[-1] if mandates else {}
    closed = len(orders)
    wins = sum(1 for order in orders if order.get("win"))
    win_rate = wins / closed * 100 if closed else 0.0
    guard_label = "Capital brake ON" if guard["lockout"] else "Risk tightened" if guard["active"] else "Normal scan"
    st.markdown("### Judge Brief")
    st.markdown(
        f"""
        <div class="vc-stack">
            <div class="vc-panel">
                <h3>What VesperClaw Is</h3>
                <p>Autonomous Bitget paper agent with explainable mandates, AgentVault risk firewall,
                refusal scoring, evolution memory, prediction markets, and Meme Radar.</p>
            </div>
            <div class="vc-panel">
                <h3>Current Safety State</h3>
                <p><strong>{safe_text(guard_label)}</strong>: {safe_text(guard['reason'])}.
                Alpha Gate now requires {safe_text(config.HTF_TIMEFRAME)} trend confirmation before any new entry.
                Live trading remains disabled; Bitget keys are read-only.</p>
            </div>
            <div class="vc-panel">
                <h3>Proof To Inspect</h3>
                <p>Paper PnL {pnl['total']:+,.2f}, closed trades {closed}, win rate {win_rate:.1f}%.
                Latest mandate: {safe_text(latest.get('action', 'waiting'))} /
                {safe_text(latest.get('vault', {}).get('decision', 'idle'))}.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def alpha_gate_mandate_panel(mandates: list[dict]) -> None:
    st.markdown("### Alpha Gate + Latest Mandate")
    latest = mandates[-1] if mandates else {}
    snap = latest.get("snapshot", {}) if isinstance(latest, dict) else {}
    vault = latest.get("vault", {}) if isinstance(latest, dict) else {}
    action = latest.get("action", "WAITING") if latest else "WAITING"
    regime = latest.get("regime", "waiting") if latest else "waiting"
    thesis = latest.get("thesis", "Waiting for the next loop cycle to write a mandate.") if latest else "Waiting for the next loop cycle to write a mandate."
    counter = latest.get("counterargument", "No counterargument recorded yet.") if latest else "No counterargument recorded yet."
    alpha_text = "PASSED" if "Alpha Gate passed" in thesis else "BLOCKING WEAK SETUPS"
    alpha_color = "#20e3b2" if alpha_text == "PASSED" else "#ffd166"
    direction_check = "1h trend confirmation"
    volume_check = "volume not falling"
    strength_check = f"ADX >= {config.MIN_TREND_ADX:g}"

    left, right = st.columns([1, 1.45])
    with left:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>{badge(f"ALPHA GATE {alpha_text}", alpha_color)}</h3>
                <p>
                    This is the quality filter before AgentVault. It blocks choppy or weak
                    crypto trades unless trend, strength, volume, and higher-timeframe direction agree.
                </p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Trend</span><strong>{safe_text(regime)}</strong></div>
                    <div class="vc-mini"><span>HTF Rule</span><strong>{safe_text(direction_check)}</strong></div>
                    <div class="vc-mini"><span>Strength</span><strong>{safe_text(strength_check)}</strong></div>
                </div>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Volume</span><strong>{safe_text(volume_check)}</strong></div>
                    <div class="vc-mini"><span>RSI Guard</span><strong>avoid overextension</strong></div>
                    <div class="vc-mini"><span>SOL</span><strong>still in basket</strong></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>
                    {badge("LATEST MANDATE", "#22d3ee")}
                    {badge(action, ACTION_COLORS.get(action, "#8b98b8"))}
                    {badge(vault.get("decision", "VAULT IDLE"), VAULT_COLORS.get(vault.get("decision"), "#8b98b8"))}
                </h3>
                <p><strong>ID:</strong> {safe_text(latest.get('mandate_id', 'waiting'))}</p>
                <p><strong>Symbol:</strong> {safe_text(latest.get('symbol', 'waiting'))}
                | <strong>Confidence:</strong> {safe_text(latest.get('confidence', 'n/a'))}
                | <strong>Price:</strong> {safe_text(snap.get('price', 'n/a'))}</p>
                <div class="vc-rule"></div>
                <p><strong>Thesis:</strong> {safe_text(thesis)}</p>
                <p><strong>Counterargument:</strong> {safe_text(counter)}</p>
                <p><strong>Vault reason:</strong> {safe_text(vault.get('reason', 'No vault decision recorded yet.'))}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def profit_guard_panel(portfolio: dict) -> None:
    guard = profit_guard_summary(portfolio)
    color = "#ff5470" if guard["lockout"] else "#ffd166" if guard["active"] else "#20e3b2"
    label = "LOCKOUT" if guard["lockout"] else "ACTIVE" if guard["active"] else "CLEAR"
    st.markdown(
        f"""
        <div class="vc-panel">
            <h3>{badge(f"PROFIT GUARD {label}", color)}</h3>
            <p>
                {safe_text(guard['reason'])}. When active, VesperClaw raises the confidence floor,
                blocks configured choppy regimes, and caps new position size at
                {config.PROFIT_GUARD_MAX_SIZE_PCT:.1%}.
            </p>
            <div class="vc-ledger-stat">
                <div class="vc-mini"><span>Loss Streak</span><strong>{guard['loss_streak']}</strong></div>
                <div class="vc-mini"><span>Drawdown</span><strong>{guard['drawdown_pct']:.2f}%</strong></div>
                <div class="vc-mini"><span>Daily Loss</span><strong>{guard['daily_loss_pct']:.2f}%</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def meme_radar_panel(portfolio: dict) -> None:
    st.markdown("### Meme Radar")
    st.markdown(
        """
        <div class="vc-callout">
            Search a meme coin ticker or name. VesperClaw checks trending status,
            liquidity, market-cap floor, momentum, volatility, and the current
            Profit Guard state before returning a buy/watch/avoid verdict.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")

    guard = profit_guard_summary(portfolio)
    left, right = st.columns([2.2, 0.8])
    with left:
        query = st.text_input(
            "Meme coin search",
            value="",
            placeholder="Try PEPE, WIF, BONK, DOGE, SHIB...",
        )
    with right:
        st.caption("Source: CoinGecko public market/trending data")
        scan_clicked = st.button("Scan Meme Coin", use_container_width=True)

    query = query.strip()
    should_scan = scan_clicked or bool(query)
    if not should_scan:
        st.caption("Showing the current meme-token watchlist until you search.")

    with st.spinner("VesperClaw is checking liquidity, momentum, and risk gates..."):
        result = cached_meme_scan(query, bool(guard["active"]), str(guard["reason"]))

    if not result.get("ok"):
        st.warning(result.get("error", "Meme Radar is unavailable right now."))
        return

    selected = result.get("selected", {})
    verdict = selected.get("verdict", "WATCH")
    verdict_color = {
        "BUY CANDIDATE": "#20e3b2",
        "WATCH": "#ffd166",
        "AVOID": "#ff5470",
    }.get(verdict, "#8b98b8")
    name = selected.get("name") or "Unknown"
    symbol = selected.get("symbol") or "n/a"
    score = selected.get("score", 0)
    price = selected.get("price")
    price_text = f"${float(price):,.8f}" if isinstance(price, (int, float)) and price < 1 else compact_usd(price)
    positives = "".join(f"<li>{safe_text(item)}</li>" for item in selected.get("positives", []))
    warnings = "".join(f"<li>{safe_text(item)}</li>" for item in selected.get("warnings", []))
    if not positives:
        positives = "<li>No strong positive gate fired yet.</li>"
    if not warnings:
        warnings = "<li>No major red flag detected by the scanner.</li>"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Radar score", f"{score:.1f}/100")
    c2.metric("Verdict", verdict)
    c3.metric("Market cap", compact_usd(selected.get("market_cap")))
    c4.metric("24h volume", compact_usd(selected.get("volume")))
    c5.metric("24h move", pct(selected.get("change_24h")))

    guard_note = selected.get("guard_note", "")
    st.markdown(
        f"""
        <div class="vc-verdict" style="--verdict-color:{verdict_color};">
            <div class="vc-verdict-title">{safe_text(verdict)} - {safe_text(name)} ({safe_text(symbol)})</div>
            <div class="vc-caption">
                Price {safe_text(price_text)} | 1h {pct(selected.get('change_1h'))} |
                7d {pct(selected.get('change_7d'))} |
                Volume/MCap {_as_percent(selected.get('vol_to_cap'))}
            </div>
            <div class="vc-ledger-stat">
                <div class="vc-mini"><span>Why it can work</span><ul class="vc-list">{positives}</ul></div>
                <div class="vc-mini"><span>Why it can fail</span><ul class="vc-list">{warnings}</ul></div>
                <div class="vc-mini"><span>Execution stance</span><strong>{safe_text(_meme_execution_stance(verdict, guard_note))}</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    candidates = result.get("candidates", [])
    if candidates:
        rows = [
            {
                "coin": f"{row.get('name')} ({row.get('symbol')})",
                "verdict": row.get("verdict"),
                "score": row.get("score"),
                "price": row.get("price"),
                "market_cap": row.get("market_cap"),
                "volume": row.get("volume"),
                "1h": row.get("change_1h"),
                "24h": row.get("change_24h"),
                "7d": row.get("change_7d"),
            }
            for row in candidates
        ]
        st.markdown("#### Meme Watchlist")
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100),
                "price": st.column_config.NumberColumn("price", format="$%.8f"),
                "market_cap": st.column_config.NumberColumn("market cap", format="$%.0f"),
                "volume": st.column_config.NumberColumn("24h volume", format="$%.0f"),
                "1h": st.column_config.NumberColumn("1h %", format="%+.2f%%"),
                "24h": st.column_config.NumberColumn("24h %", format="%+.2f%%"),
                "7d": st.column_config.NumberColumn("7d %", format="%+.2f%%"),
            },
        )


def _as_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _meme_execution_stance(verdict: str, guard_note: str = "") -> str:
    if guard_note:
        return guard_note
    if verdict == "BUY CANDIDATE":
        return "Paper-buy candidate only; small size, tight invalidation."
    if verdict == "WATCH":
        return "Wait for cleaner liquidity/momentum confirmation."
    return "No buy; risk gates are not paying for the upside."


def loop_map_panel(portfolio: dict, mandates: list[dict], orders: list[dict], evo: list[dict]) -> None:
    latest = mandates[-1] if mandates else {}
    latest_order = orders[-1] if orders else {}
    latest_evo = evo[-1] if evo else {}
    guard = profit_guard_summary(portfolio)
    stages = [
        ("1", "Perceive", f"{latest.get('symbol', 'n/a')} / {latest.get('regime', 'booting')}"),
        ("2", "Propose", f"{latest.get('action', 'n/a')} @ conf {latest.get('confidence', 'n/a')}"),
        ("3", "Verify", latest.get("vault", {}).get("decision", "n/a")),
        ("4", "Execute", f"{latest_order.get('symbol', 'n/a')} PnL {latest_order.get('pnl', 'n/a')}"),
        ("5", "Monitor", "Guard " + ("LOCKOUT" if guard["lockout"] else "ACTIVE" if guard["active"] else "CLEAR")),
        ("6", "Learn", latest_evo.get("reason", "waiting for samples")),
    ]
    cards = "".join(
        f'<div class="vc-panel"><h3><span class="vc-step-num">{num}</span>'
        f'{safe_text(name)}</h3><p>{safe_text(detail)}</p></div>'
        for num, name, detail in stages
    )
    st.markdown("### Loop Engine")
    st.markdown(
        """
        <div class="vc-callout">
            VesperClaw is loop-engineered: it does not ask an LLM for a trade and stop.
            It runs a self-checking cycle that perceives, proposes, verifies, executes,
            monitors risk, and writes lessons back to memory.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    st.markdown(f'<div class="vc-stack">{cards}</div>', unsafe_allow_html=True)


def loop_state_panel() -> None:
    state_path = data_file("LOOP_STATE_FILE", "LOOP_STATE.md")
    if not os.path.exists(state_path):
        try:
            loop_state.write_loop_state()
        except Exception:  # noqa: BLE001
            return
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state_text = f.read()
    except OSError:
        return
    st.markdown("### Loop State Memory")
    st.caption("Human-readable state file generated from the JSON audit trail.")
    st.download_button(
        "Download LOOP_STATE.md",
        state_text,
        file_name="LOOP_STATE.md",
        mime="text/markdown",
    )
    with st.expander("Preview LOOP_STATE.md", expanded=False):
        st.markdown(state_text)


def agent_hub_panel() -> None:
    data = store.read_json(data_file("AGENT_HUB_STATUS_FILE", "agent_hub_status.json"), {})
    if not data:
        try:
            data = agent_hub.write_status()
        except Exception:  # noqa: BLE001
            data = {}
    live_creds = agent_hub.credential_status()
    if isinstance(data, dict):
        data["credentials"] = live_creds
    cli = data.get("cli", {}) if isinstance(data, dict) else {}
    creds = data.get("credentials", {}) if isinstance(data, dict) else {}
    skills = data.get("skills", []) if isinstance(data, dict) else []
    cli_label = "CONNECTED" if cli.get("available") else "ADAPTER READY"
    cli_color = "#20e3b2" if cli.get("available") else "#ffd166"
    st.markdown("### Bitget Agent Hub")
    st.markdown(
        f"""
        <div class="vc-panel">
            <h3>{badge(f"AGENT HUB {cli_label}", cli_color)}</h3>
            <p>
                Official Bitget Hub integration surface. VesperClaw keeps execution
                <strong>{safe_text(creds.get('mode', 'paper-only-safe'))}</strong> unless
                real trading is explicitly enabled.
            </p>
            <div class="vc-ledger-stat">
                <div class="vc-mini"><span>CLI</span><strong>{safe_text(cli.get('version', 'not detected'))}</strong></div>
                <div class="vc-mini"><span>API Keys</span><strong>{'ready (read-only)' if creds.get('read_ready') else 'not set'}</strong></div>
                <div class="vc-mini"><span>Skills</span><strong>{len(skills)}/5 lanes</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if skills:
        rows = [
            {
                "skill": s.get("id"),
                "capability": s.get("capability"),
                "status": s.get("status"),
                "vesperclaw_source": s.get("vesperclaw_source"),
            }
            for s in skills
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def trust_command_center(
    portfolio: dict,
    mandates: list[dict],
    orders: list[dict],
    saves: list[dict],
    evo: list[dict],
) -> None:
    metrics = conviction_metrics(portfolio, orders, saves)
    latest = mandates[-1] if mandates else {}
    digest = mandate_fingerprint(latest) if latest else "0" * 64
    latest_id = latest.get("mandate_id", "no-mandate")
    latest_vault = latest.get("vault", {}).get("decision", "IDLE")

    left, right = st.columns([0.95, 1.45])
    with left:
        st.markdown(
            f"""
            <div class="vc-score-card">
                <div class="vc-score-ring" style="--score: {metrics['score']};">
                    <div class="vc-score-ring-inner">
                        <div>
                            <div class="vc-score-value">{metrics['score']}</div>
                            <div class="vc-score-label">Conviction Score</div>
                        </div>
                    </div>
                </div>
                <p>Composite of win rate, refusal accuracy, drawdown discipline, and audit evidence.</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Refusal Accuracy</span><strong>{metrics['refusal_accuracy']}%</strong></div>
                    <div class="vc-mini"><span>Drawdown</span><strong>{metrics['drawdown_pct']}%</strong></div>
                    <div class="vc-mini"><span>Evidence</span><strong>{metrics['evidence']}%</strong></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown("### Agent Brain")
        events = agent_brain_events(mandates, orders, saves, evo)
        if events:
            body = "".join(
                f"<div class='vc-brain-line'><span>[{safe_text(event['time'])}]</span>{safe_text(event['text'])}</div>"
                for event in events[:12]
            )
            st.markdown(f"<div class='vc-brain'>{body}</div>", unsafe_allow_html=True)
        else:
            st.caption("Agent brain feed will appear once the loop writes mandates.")

        st.markdown("### Mandate Fingerprint")
        st.markdown(
            f"""
            <div class="vc-panel">
                <p><strong>Latest mandate:</strong> {safe_text(latest_id)} | <strong>Vault:</strong> {safe_text(latest_vault)}</p>
                <p class="vc-caption">SHA-256 over the latest mandate JSON. This makes the audit trail tamper-evident for demos and reviews.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.code(digest, language="text")


def _event_time(value: str | None) -> str:
    if not value:
        return "--:--:--"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return str(value)[11:19] if len(str(value)) >= 19 else str(value)


def agent_brain_events(
    mandates: list[dict],
    orders: list[dict],
    saves: list[dict],
    evo: list[dict],
) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []

    for mandate in mandates[-5:]:
        snap = mandate.get("snapshot", {})
        symbol = mandate.get("symbol", "?")
        action = mandate.get("action", "NO_TRADE")
        regime = mandate.get("regime", "unknown")
        vault = mandate.get("vault", {})
        news_source = snap.get("news_source", "news")
        news_count = snap.get("news_count", 0)
        events.append(
            {
                "time": _event_time(mandate.get("timestamp")),
                "text": (
                    f"Scanner: {symbol} regime={regime}, action={action}, "
                    f"conf={mandate.get('confidence', 'n/a')}, news={news_count} {news_source}."
                ),
            }
        )
        if mandate.get("counterargument"):
            events.append(
                {
                    "time": _event_time(mandate.get("timestamp")),
                    "text": f"Debate: counterargument logged for {symbol}: {mandate.get('counterargument')}",
                }
            )
        if vault.get("decision"):
            events.append(
                {
                    "time": _event_time(mandate.get("timestamp")),
                    "text": f"AgentVault: {vault.get('decision')} - {vault.get('reason', 'no reason recorded')}",
                }
            )

    for order in orders[-4:]:
        events.append(
            {
                "time": _event_time(order.get("exit_time") or order.get("timestamp")),
                "text": (
                    f"Execution: closed {order.get('direction')} {order.get('symbol')} "
                    f"via {order.get('reason')} with PnL ${float(order.get('pnl', 0)):+.2f}."
                ),
            }
        )

    for save in saves[-4:]:
        verdict = save.get("verdict", "pending")
        events.append(
            {
                "time": _event_time(save.get("timestamp")),
                "text": (
                    f"Conviction Ledger: refused {save.get('direction')} {save.get('symbol')} "
                    f"because '{save.get('reason')}', verdict={verdict}."
                ),
            }
        )

    for entry in evo[-3:]:
        events.append(
            {
                "time": _event_time(entry.get("timestamp")),
                "text": f"Evolution Engine: {entry.get('reason')}",
            }
        )

    return sorted(events, key=lambda item: item["time"], reverse=True)


def accountability_loop(mandates: list[dict], orders: list[dict], saves: list[dict], evo: list[dict]) -> None:
    blocked = len(saves) if isinstance(saves, list) else 0
    resolved = sum(1 for save in saves if save.get("resolved")) if isinstance(saves, list) else 0
    good = sum(1 for save in saves if save.get("verdict") == "good_block") if isinstance(saves, list) else 0
    bad = sum(1 for save in saves if save.get("verdict") == "bad_block") if isinstance(saves, list) else 0
    latest_decision = latest_vault_decision(mandates)

    st.markdown("### The Accountability Loop")
    st.markdown(
        """
        <div class="vc-callout">
            <strong>VesperClaw does three things judges can inspect:</strong>
            it blocks unsafe trades, scores whether restraint was right, then updates future strategy trust from closed outcomes.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    st.markdown(
        f"""
        <div class="vc-stack">
            <div class="vc-panel">
                <h3><span class="vc-step-num">1</span>AgentVault Safety</h3>
                <p>Every mandate must clear hard risk gates before execution.</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Latest Gate</span><strong>{safe_text(latest_decision)}</strong></div>
                    <div class="vc-mini"><span>Blocked</span><strong>{blocked}</strong></div>
                    <div class="vc-mini"><span>Closed Trades</span><strong>{len(orders)}</strong></div>
                </div>
            </div>
            <div class="vc-panel">
                <h3><span class="vc-step-num">2</span>Conviction Ledger</h3>
                <p>Refused trades are not forgotten; they are reconciled against market outcomes.</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Resolved</span><strong>{resolved}</strong></div>
                    <div class="vc-mini"><span>Good Blocks</span><strong>{good}</strong></div>
                    <div class="vc-mini"><span>Bad Blocks</span><strong>{bad}</strong></div>
                </div>
            </div>
            <div class="vc-panel">
                <h3><span class="vc-step-num">3</span>Evolution Engine</h3>
                <p>Strategy weights move only after closed trades, per regime, with capped updates.</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Min Samples</span><strong>{config.EVO_MIN_SAMPLES}</strong></div>
                    <div class="vc-mini"><span>Step Cap</span><strong>{config.EVO_STEP_CAP:.0%}</strong></div>
                    <div class="vc-mini"><span>Updates</span><strong>{len(evo)}</strong></div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def accountability_hero() -> None:
    ledger = briefing_mod.build_ledger()
    taken, refused = ledger["taken"], ledger["refused"]
    brief = store.read_json(data_file("BRIEFING_FILE", "briefing.json"), {})

    st.markdown("### Conviction Ledger")
    st.markdown('<div class="vc-caption">The scoreboard for action and inaction.</div>', unsafe_allow_html=True)
    left, right = st.columns(2)

    with left:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>{badge("TRADES TAKEN", "#20e3b2")}</h3>
                <p>{safe_text(ledger['headline'])}</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Count</span><strong>{taken['count']}</strong></div>
                    <div class="vc-mini"><span>Win Rate</span><strong>{taken['win_rate']}%</strong></div>
                    <div class="vc-mini"><span>Net PnL</span><strong>${taken['pnl']:,.2f}</strong></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>{badge("TRADES REFUSED", "#ff5470")}</h3>
                <p>Every block is later reconciled against the market move it avoided or missed.</p>
                <div class="vc-ledger-stat">
                    <div class="vc-mini"><span>Refused</span><strong>{refused['count']}</strong></div>
                    <div class="vc-mini"><span>Correct</span><strong>{refused['refusal_accuracy_pct']}%</strong></div>
                    <div class="vc-mini"><span>Avg Avoided</span><strong>{refused['avg_adverse_move_avoided_pct']}%</strong></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if brief.get("text"):
        st.info(brief["text"])
        st.caption(f"Self-briefing filed {brief.get('timestamp', '')}")
    else:
        st.caption("Self-briefing will appear once the agent has run a few cycles.")


def vault_saves_panel(saves: list[dict]) -> None:
    st.markdown("### Vault Saves")
    if not saves:
        st.caption("No blocked or downsized trades recorded yet.")
        return
    rows = []
    for save in reversed(saves[-10:]):
        rows.append(
            {
                "mandate": save.get("mandate_id"),
                "symbol": save.get("symbol"),
                "direction": save.get("direction"),
                "decision": save.get("decision"),
                "verdict": save.get("verdict", "pending"),
                "would_be_pnl_pct": save.get("would_be_pnl_pct"),
                "reason": save.get("reason"),
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "would_be_pnl_pct": st.column_config.NumberColumn("would-be PnL %", format="%.3f"),
        },
    )


def basket_panel(mandates: list[dict]) -> None:
    st.markdown("### Market Scanner")
    if not mandates:
        st.info("No cycles recorded yet. Start the loop: `python main.py --mode fast_demo`")
        return

    latest_by_symbol: dict[str, dict] = {}
    for mandate in mandates:
        latest_by_symbol[mandate["symbol"]] = mandate

    rows = []
    for sym, mandate in latest_by_symbol.items():
        vault = mandate.get("vault", {})
        rows.append(
            {
                "symbol": sym,
                "regime": mandate.get("regime"),
                "action": mandate.get("action"),
                "confidence": mandate.get("confidence"),
                "vault": vault.get("decision", "-"),
                "price": mandate.get("entry_price"),
                "rr": mandate.get("rr"),
                "mandate": mandate.get("mandate_id"),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
            "price": st.column_config.NumberColumn("price", format="$%.4f"),
            "rr": st.column_config.NumberColumn("R:R", format="%.2f"),
        },
    )


def latest_decision(mandates: list[dict]) -> None:
    st.markdown("### Latest Mandate")
    if not mandates:
        st.info("No mandates yet. Start the loop: `python main.py --mode fast_demo --demo-data --reset`")
        return

    mandate = mandates[-1]
    snap = mandate.get("snapshot", {})
    vault = mandate.get("vault", {})
    action = mandate.get("action", "NO_TRADE")
    regime = mandate.get("regime", "uncertain")

    left, right = st.columns([1.35, 1])
    with left:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>
                    {badge(action, ACTION_COLORS.get(action, "#8b98b8"))}
                    {badge(regime, REGIME_COLORS.get(regime, "#8b98b8"))}
                    {badge(mandate.get("mandate_id", "NO_ID"), "#22d3ee")}
                </h3>
                <p><strong>Thesis:</strong> {safe_text(mandate.get('thesis'))}</p>
                <div class="vc-rule"></div>
                <p><strong>Counterargument:</strong> {safe_text(mandate.get('counterargument'))}</p>
                <p><strong>Invalidation:</strong> {safe_text(mandate.get('invalidation'))}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if action != "NO_TRADE":
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry", f"{mandate.get('entry_price')}")
            c2.metric("Stop", f"{mandate.get('stop_loss')}", pct(mandate.get("stop_loss_pct")))
            c3.metric("Target", f"{mandate.get('take_profit')}", pct(mandate.get("take_profit_pct")))
            c4.metric("Size", f"{mandate.get('requested_size_pct', 0) * 100:.2f}%")

        votes = mandate.get("agent_votes", {})
        if votes:
            vote_df = pd.DataFrame([{"agent": k, "vote": v} for k, v in votes.items()])
            st.dataframe(vote_df, hide_index=True, use_container_width=True)

    with right:
        st.markdown(
            f"""
            <div class="vc-panel">
                <h3>{badge("AGENTVAULT", VAULT_COLORS.get(vault.get('decision'), '#8b98b8'))}</h3>
                <p><strong>{vault.get('decision', 'IDLE')}</strong></p>
                <p>{safe_text(vault.get('reason'), 'No vault decision recorded yet.')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        checks = vault.get("checks", {})
        if checks:
            check_df = pd.DataFrame(
                [{"check": k, "status": "PASS" if v else "FAIL"} for k, v in checks.items()]
            )
            st.dataframe(check_df, hide_index=True, use_container_width=True)

        snapshot_rows = {
            "price": snap.get("price"),
            "ADX": snap.get("adx"),
            "RSI": snap.get("rsi"),
            "ATR%": snap.get("atr_pct"),
            "EMA fast": snap.get("ema_fast"),
            "EMA slow": snap.get("ema_slow"),
            "Fear & Greed": f"{snap.get('fear_greed')} ({snap.get('fg_class')})"
            if snap.get("fear_greed") is not None
            else "n/a",
            "funding rate": snap.get("funding_rate"),
            "news": (
                f"{snap.get('news_count', 0)} {snap.get('news_source', 'news')} headlines "
                f"/ bias {snap.get('news_bias', 0)}"
            ),
        }
        st.json(snapshot_rows, expanded=False)
        if snap.get("headlines"):
            st.caption("Headlines: " + " | ".join(snap["headlines"][:3]))


def equity_curve(mandates: list[dict]) -> None:
    st.markdown("### Equity Curve")
    if not mandates:
        st.caption("Waiting for mandates.")
        return

    df = pd.DataFrame(
        {
            "cycle": list(range(1, len(mandates) + 1)),
            "equity": [m.get("equity", config.INITIAL_BALANCE) for m in mandates],
        }
    )
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["cycle"],
            y=df["equity"],
            mode="lines",
            line=dict(color="#20e3b2", width=3),
            fill="tozeroy",
            fillcolor="rgba(32, 227, 178, 0.08)",
            hovertemplate="Cycle %{x}<br>Equity $%{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=330,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#dce8ff"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
        yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
    )
    st.plotly_chart(fig, use_container_width=True)


def weights_panel() -> None:
    st.markdown("### Learning Weights")
    summ = evolution.summary()
    rows = []
    for regime, row in summ["weights"].items():
        entry = {"regime": regime}
        entry.update({k: round(v, 3) for k, v in row.items()})
        rows.append(entry)
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Vault saves", summ["vault_saves_good"])
    c2.metric("Bad blocks", summ["vault_saves_bad"])
    c3.metric("Pending", summ["vault_saves_pending"])


def evolution_readiness_panel() -> None:
    st.markdown("### Evolution Readiness")
    summ = evolution.summary()
    rows = []
    for regime, agents in summ["stats"].items():
        weights = summ["weights"].get(regime, {})
        for agent, stats in agents.items():
            samples = int(stats.get("samples", 0))
            wins = int(stats.get("wins", 0))
            pnl = float(stats.get("pnl", 0.0))
            next_in = config.EVO_MIN_SAMPLES - (samples % config.EVO_MIN_SAMPLES)
            if samples and samples % config.EVO_MIN_SAMPLES == 0:
                next_in = config.EVO_MIN_SAMPLES
            rows.append(
                {
                    "regime": regime,
                    "agent": agent,
                    "samples": samples,
                    "wins": wins,
                    "win_rate_pct": (wins / samples * 100) if samples else 0.0,
                    "avg_pnl_pct": pnl / samples if samples else 0.0,
                    "next_update_in": next_in,
                    "weight_pct": (weights.get(agent, 0.0) * 100),
                }
            )

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "win_rate_pct": st.column_config.ProgressColumn("win rate", min_value=0, max_value=100, format="%.0f%%"),
            "avg_pnl_pct": st.column_config.NumberColumn("avg PnL %", format="%.3f"),
            "weight_pct": st.column_config.ProgressColumn("weight", min_value=0, max_value=100, format="%.0f%%"),
        },
    )
    st.caption(
        f"Learning rule: update only every {config.EVO_MIN_SAMPLES} closed samples per regime/agent, "
        f"cap each move at {config.EVO_STEP_CAP:.0%}, keep every agent above {config.EVO_WEIGHT_FLOOR:.0%}."
    )


def evolution_log(evo: list[dict]) -> None:
    st.markdown("### Evolution Log")
    if not evo:
        st.caption("No weight changes yet; the engine needs enough closed trades per regime.")
        return
    for entry in reversed(evo[-10:]):
        st.markdown(
            f"- `{entry['regime']}` | **{entry['agent']}** "
            f"{entry['old_weight']} -> {entry['new_weight']} | {entry['reason']}"
        )


def trade_log() -> None:
    st.markdown("### Trade Log")
    if not os.path.exists(config.TRADE_LOG_CSV):
        st.caption("No fills logged yet.")
        return
    df = pd.read_csv(config.TRADE_LOG_CSV)
    st.dataframe(df.tail(50), hide_index=True, use_container_width=True)
    st.download_button(
        "Download trade_log.csv",
        df.to_csv(index=False),
        file_name="trade_log.csv",
        mime="text/csv",
    )


def mandates_table(mandates: list[dict]) -> None:
    st.markdown("### Mandate Ledger")
    if not mandates:
        st.caption("No mandates recorded yet.")
        return
    rows = [
        {
            "id": m["mandate_id"],
            "symbol": m.get("symbol"),
            "action": m.get("action"),
            "regime": m.get("regime"),
            "confidence": m.get("confidence"),
            "vault": m.get("vault", {}).get("decision"),
            "equity": m.get("equity"),
        }
        for m in mandates[-50:]
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
            "equity": st.column_config.NumberColumn("equity", format="$%.2f"),
        },
    )


def prediction_panel() -> None:
    pf = store.read_json(data_file("PRED_PORTFOLIO_FILE", "pred_portfolio.json"), {})
    mandates = store.read_json(data_file("PRED_MANDATES_FILE", "pred_mandates.json"), [])
    orders = store.read_json(data_file("PRED_ORDERS_FILE", "pred_orders.json"), [])
    mandates = mandates if isinstance(mandates, list) else []
    orders = orders if isinstance(orders, list) else []
    st.markdown("### Prediction Markets")
    eq = pf.get("equity", config.PRED_INITIAL_BALANCE)
    closed = len(orders)
    wins = sum(1 for order in orders if order.get("win"))
    accuracy = (wins / closed * 100) if closed else 0.0
    pred_pnl = round(float(eq) - config.PRED_INITIAL_BALANCE, 2)
    approved = sum(1 for m in mandates if m.get("vault", {}).get("decision") == "APPROVED")
    rejected = sum(1 for m in mandates if m.get("vault", {}).get("decision") == "REJECTED")
    total_seen = approved + rejected
    approve_rate = (approved / total_seen * 100) if total_seen else 0.0
    football_seen = sum(1 for m in mandates if m.get("topic") == "football")
    football_open = sum(1 for p in pf.get("open_positions", []) if p.get("topic") == "football")
    world_cup_seen = sum(1 for m in mandates if m.get("topic") == "world_cup")
    world_cup_open = sum(1 for p in pf.get("open_positions", []) if p.get("topic") == "world_cup")

    st.markdown(
        f"""
        <div class="vc-callout">
            <strong>90% target mode:</strong> the Probability Agent only paper-trades when
            estimated edge and confidence clear the high-accuracy gate. Weak markets become
            visible refusals instead of hidden non-events.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Prediction equity", f"${eq:,.2f}", f"{(eq / config.PRED_INITIAL_BALANCE - 1) * 100:+.2f}%")
    c2.metric("Prediction PnL", f"${pred_pnl:+,.2f}")
    c3.metric("Observed accuracy", f"{accuracy:.1f}%", f"target {config.PRED_TARGET_ACCURACY:.0%}")
    c4.metric("Closed / Open", f"{closed} / {len(pf.get('open_positions', []))}")
    c5.metric("Approved rate", f"{approve_rate:.1f}%", f"{rejected} refused")
    c6.metric("World Cup", world_cup_seen, f"{world_cup_open} open")
    c7.metric("Football", football_seen, f"{football_open} open")

    try:
        live_world_cup = cached_world_cup_board(max(config.PRED_WORLD_CUP_MARKETS, 12))
    except Exception:  # noqa: BLE001
        live_world_cup = []
    world_cup_mandates = [m for m in mandates if m.get("topic") == "world_cup"]
    world_cup_rows = []
    for m in world_cup_mandates[-12:]:
        world_cup_rows.append(
            {
                "lane": m.get("market_kind", "world_cup"),
                "market": m.get("market", "")[:82],
                "yes": m.get("yes_price"),
                "agent": m.get("est_prob"),
                "edge": m.get("edge"),
                "confidence": m.get("confidence"),
                "decision": m.get("vault", {}).get("decision"),
                "gate": m.get("vault", {}).get("reason"),
            }
        )
    seen_markets = {row["market"] for row in world_cup_rows}
    for m in live_world_cup:
        label = m.get("question", "")[:82]
        if label in seen_markets:
            continue
        world_cup_rows.append(
            {
                "lane": m.get("market_kind", "world_cup"),
                "market": label,
                "yes": m.get("yes_price"),
                "agent": None,
                "edge": None,
                "confidence": None,
                "decision": "WATCHLIST",
                "gate": "live market queued for next probability cycle",
            }
        )
        if len(world_cup_rows) >= 12:
            break

    if world_cup_rows:
        st.markdown("#### World Cup Board")
        st.caption("Country winner, player scorer/award, and match-prop markets are refreshed from the live prediction feed.")
        st.dataframe(
            pd.DataFrame(world_cup_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "yes": st.column_config.NumberColumn("market yes", format="%.3f"),
                "agent": st.column_config.NumberColumn("agent estimate", format="%.3f"),
                "edge": st.column_config.NumberColumn("edge", format="%+.3f"),
                "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
            },
        )

    if mandates:
        rows = [
            {
                "topic": m.get("topic", "general"),
                "lane": m.get("market_kind", m.get("topic", "general")),
                "market": m.get("market", "")[:70],
                "yes": m.get("yes_price"),
                "estimate": m.get("est_prob"),
                "edge": m.get("edge"),
                "confidence": m.get("confidence"),
                "action": m.get("action"),
                "decision": m.get("vault", {}).get("decision"),
                "gate": m.get("vault", {}).get("reason"),
            }
            for m in mandates[-12:]
        ]
        st.markdown("#### Probability Agent Decisions")
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "yes": st.column_config.NumberColumn("market yes", format="%.3f"),
                "estimate": st.column_config.NumberColumn("agent estimate", format="%.3f"),
                "edge": st.column_config.NumberColumn("edge", format="%+.3f"),
                "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
            },
        )

    if pf.get("open_positions"):
        st.markdown("#### Open Prediction Positions")
        open_rows = [
            {
                "topic": p.get("topic", "general"),
                "lane": p.get("market_kind", p.get("topic", "general")),
                "market": p.get("question", "")[:70],
                "side": p.get("side"),
                "entry_yes": p.get("entry_yes"),
                "last_yes": pf.get("last_yes", {}).get(p.get("market_id"), p.get("entry_yes")),
                "target_yes": p.get("target_yes"),
                "stop_yes": p.get("stop_yes"),
                "confidence": p.get("confidence"),
                "stake": p.get("stake"),
            }
            for p in pf.get("open_positions", [])
        ]
        st.dataframe(pd.DataFrame(open_rows), hide_index=True, use_container_width=True)

    if orders:
        st.markdown("#### Closed Prediction Outcomes")
        closed_rows = [
            {
                "topic": o.get("topic", "general"),
                "lane": o.get("market_kind", o.get("topic", "general")),
                "market": o.get("question", "")[:70],
                "side": o.get("side"),
                "entry_yes": o.get("entry_yes"),
                "exit_yes": o.get("exit_yes"),
                "pnl": o.get("pnl"),
                "win": o.get("win"),
                "reason": o.get("reason"),
                "confidence": o.get("confidence"),
            }
            for o in orders[-20:]
        ]
        st.dataframe(
            pd.DataFrame(closed_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "pnl": st.column_config.NumberColumn("PnL", format="$%+.2f"),
                "confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
            },
        )


def contract_command_console() -> None:
    profile_doc = store.read_json(data_file("PROFILE_FILE", "profile.json"), {})
    source = profile_doc.get("source", "") if isinstance(profile_doc, dict) else ""
    overrides = profile_doc.get("overrides", {}) if isinstance(profile_doc, dict) else {}

    st.markdown("### Contract Command")
    st.caption("Natural-language control for the paper perpetuals agent. The compiler can tune settings, but AgentVault still enforces hard risk limits.")
    if source:
        st.caption(f"Active command: {source}")
    if overrides:
        st.json(overrides, expanded=False)

    prompt = st.text_area(
        "Describe the contract trading style",
        value="High-conviction BTC and ETH perpetuals only, max 3x leverage, avoid choppy markets, smaller size after losses.",
        height=90,
        label_visibility="collapsed",
    )
    if st.button("Compile Contract Profile", use_container_width=True):
        if not prompt.strip():
            st.warning("Write a contract-trading instruction first.")
            return
        profile = vibe.set_vibe(prompt.strip())
        if profile:
            st.success("Contract profile saved. The live loop applies saved profile updates on the next cycle.")
            st.json(profile, expanded=False)
        else:
            st.warning("No safe overrides were produced. The existing risk settings remain active.")


def sidebar(auto_default: bool = False) -> bool:
    with st.sidebar:
        st.markdown("## VesperClaw")
        st.caption("Glass-box controls")
        st.markdown(f"**Provider**: `{config.LLM_PROVIDER}`")
        st.markdown(f"**Basket**: `{', '.join(config.SYMBOL_ALLOWLIST)}`")
        st.markdown(f"**Timeframe**: `{config.LOOP_TIMEFRAME}`")
        st.markdown(f"**Leverage**: `{config.LEVERAGE:g}x`")
        st.markdown(f"**Mode**: `{config.RUN_MODE}`")
        st.divider()
        contract_command_console()
        st.divider()
        if st.button("Refresh terminal", use_container_width=True):
            st.rerun()
        auto = st.checkbox("Auto-refresh every 5s", value=auto_default)
        st.divider()
        st.caption("The dashboard only reads local audit artifacts. No real orders are placed.")
    return auto


def main() -> None:
    apply_css()
    auto = sidebar()
    portfolio, mandates, orders, evo, saves = load_all()

    hero(portfolio, mandates)
    st.write("")
    kpi_strip(portfolio)
    st.write("")
    alpha_gate_mandate_panel(mandates)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    judge_brief_panel(portfolio, mandates, orders)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    profit_guard_panel(portfolio)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    prediction_panel()

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    meme_radar_panel(portfolio)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    loop_map_panel(portfolio, mandates, orders, evo)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    agent_hub_panel()

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    trust_command_center(portfolio, mandates, orders, saves, evo)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    accountability_loop(mandates, orders, saves, evo)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    accountability_hero()

    vault_saves_panel(saves)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    basket_panel(mandates)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    latest_decision(mandates)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    col1, col2 = st.columns([1.15, 1])
    with col1:
        equity_curve(mandates)
    with col2:
        weights_panel()

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    evolution_readiness_panel()

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    col3, col4 = st.columns([1, 1.2])
    with col3:
        evolution_log(evo)
    with col4:
        mandates_table(mandates)

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    trade_log()

    st.markdown('<div class="vc-rule"></div>', unsafe_allow_html=True)
    loop_state_panel()

    if auto:
        import time

        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
