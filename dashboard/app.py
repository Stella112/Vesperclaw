"""VesperClaw dashboard — the glass box.

Reads the plain JSON/CSV audit trail the loop writes and renders the full
decision lifecycle: snapshot -> mandate (thesis + counterargument) -> vault
decision -> paper fills -> equity -> per-regime weights -> evolution log ->
Vault Saves. Deliberately leads with the audit trail, not the P&L.

Run:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

# allow importing the package when launched via `streamlit run`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from vesperclaw import store, evolution, briefing as briefing_mod  # noqa: E402

st.set_page_config(page_title="VesperClaw", page_icon="🦅", layout="wide")

REGIME_COLORS = {
    "trend_up": "🟢", "trend_down": "🔴", "range": "🟡", "uncertain": "⚪",
}
ACTION_COLORS = {"LONG": "🟩", "SHORT": "🟥", "NO_TRADE": "⬜"}
VAULT_COLORS = {
    "APPROVED": "✅", "APPROVED_DOWNSIZED": "🟧", "REJECTED": "⛔", "DELAYED": "⏸️",
}


def load_all():
    portfolio = store.read_json(config.PORTFOLIO_FILE, {})
    mandates = store.read_json(config.MANDATES_FILE, [])
    orders = store.read_json(config.ORDERS_FILE, [])
    evo = store.read_json(config.EVOLUTION_FILE, [])
    saves = store.read_json(config.VAULT_SAVES_FILE, [])
    return portfolio, mandates, orders, evo, saves


def header(portfolio):
    st.title("🦅 VesperClaw")
    st.caption(
        "Autonomous Bitget paper-trading agent · explainable mandates · "
        "AgentVault risk firewall · close-based per-regime learning"
    )
    eq = portfolio.get("equity", config.INITIAL_BALANCE)
    ret = (eq / config.INITIAL_BALANCE - 1) * 100
    closed = portfolio.get("closed_trades", 0)
    wr = (portfolio.get("wins", 0) / closed * 100) if closed else 0.0
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"${eq:,.2f}", f"{ret:+.2f}%")
    c2.metric("Closed trades", closed)
    c3.metric("Win rate", f"{wr:.1f}%")
    c4.metric("Open positions", len(portfolio.get("open_positions", [])))
    c5.metric("Cycle", portfolio.get("cycle", 0))


def accountability_hero():
    """THE hero: the Conviction Ledger + the agent's self-briefing.

    What makes VesperClaw different — it shows the trades it REFUSED and proves
    whether refusing was right.
    """
    ledger = briefing_mod.build_ledger()
    taken, refused = ledger["taken"], ledger["refused"]

    st.subheader("⚖️ Conviction Ledger — the agent held accountable")
    st.markdown(f"#### {ledger['headline']}")
    taken_col, refused_col = st.columns(2)
    with taken_col:
        st.markdown("##### ✅ Trades TAKEN")
        a, b, c = st.columns(3)
        a.metric("Count", taken["count"])
        b.metric("Win rate", f"{taken['win_rate']}%")
        c.metric("Net PnL", f"${taken['pnl']:,.2f}")
    with refused_col:
        st.markdown("##### ⛔ Trades REFUSED")
        a, b, c = st.columns(3)
        a.metric("Refused", refused["count"])
        b.metric("Refusals correct", f"{refused['refusal_accuracy_pct']}%",
                 help="Share of resolved refusals where the market later hit the stop it avoided")
        c.metric("Avg move avoided", f"{refused['avg_adverse_move_avoided_pct']}%")

    brief = store.read_json(config.BRIEFING_FILE, {})
    if brief.get("text"):
        st.markdown("##### 🗣️ VesperClaw's self-briefing")
        st.info(brief["text"])
        st.caption(f"filed {brief.get('timestamp','')}")
    else:
        st.caption("Self-briefing will appear once the agent has run a few cycles.")


def basket_panel(mandates):
    """Latest decision per symbol — the multi-asset scan at a glance."""
    if not mandates:
        return
    latest_by_symbol: dict[str, dict] = {}
    for m in mandates:
        latest_by_symbol[m["symbol"]] = m
    st.subheader("Basket scan (latest per symbol)")
    rows = []
    for sym, m in latest_by_symbol.items():
        v = m.get("vault", {})
        rows.append({
            "symbol": sym,
            "regime": f"{REGIME_COLORS.get(m['regime'],'')} {m['regime']}",
            "action": f"{ACTION_COLORS.get(m['action'],'')} {m['action']}",
            "conf": m["confidence"],
            "vault": f"{VAULT_COLORS.get(v.get('decision',''),'')} {v.get('decision','—')}",
            "price": m.get("entry_price"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def latest_decision(mandates):
    if not mandates:
        st.info("No cycles recorded yet. Start the loop:  `python main.py --mode fast_demo`")
        return
    m = mandates[-1]
    snap = m.get("snapshot", {})
    vault = m.get("vault", {})
    st.subheader("Latest decision")
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            f"**{ACTION_COLORS.get(m['action'],'')} {m['action']}** &nbsp; "
            f"{REGIME_COLORS.get(m['regime'],'')} regime **{m['regime']}** "
            f"(conf {m.get('regime_confidence')}) &nbsp; · &nbsp; "
            f"mandate `{m['mandate_id']}`"
        )
        st.markdown(f"**Thesis:** {m['thesis']}")
        st.markdown(f"**Counterargument:** {m['counterargument']}")
        st.markdown(f"**Invalidation:** {m['invalidation']}")
        if m["action"] != "NO_TRADE":
            st.markdown(
                f"entry `{m['entry_price']}` · SL `{m['stop_loss']}` "
                f"({m.get('stop_loss_pct')}%) · TP `{m['take_profit']}` "
                f"({m.get('take_profit_pct')}%) · R:R `{m.get('rr')}` · "
                f"size `{m.get('requested_size_pct',0)*100:.2f}%`"
            )
        votes = m.get("agent_votes", {})
        st.markdown("**Agent votes:** " + " · ".join(f"`{k}: {v}`" for k, v in votes.items()))
    with right:
        st.markdown(
            f"### {VAULT_COLORS.get(vault.get('decision',''),'')} "
            f"AgentVault: {vault.get('decision','—')}"
        )
        st.caption(vault.get("reason", ""))
        checks = vault.get("checks", {})
        if checks:
            df = pd.DataFrame(
                [{"check": k, "pass": "✅" if v else "❌"} for k, v in checks.items()]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)
        st.markdown("**Snapshot**")
        st.json({
            "price": snap.get("price"), "ADX": snap.get("adx"),
            "RSI": snap.get("rsi"), "ATR%": snap.get("atr_pct"),
            "EMA_fast": snap.get("ema_fast"), "EMA_slow": snap.get("ema_slow"),
            "Fear&Greed": f"{snap.get('fear_greed')} ({snap.get('fg_class')})"
            if snap.get("fear_greed") is not None else "n/a",
            "funding_rate": snap.get("funding_rate"),
            "news": f"{snap.get('news_count', 0)} (bias {snap.get('news_bias', 0)})",
        }, expanded=False)
        if snap.get("headlines"):
            st.caption("📰 " + " · ".join(snap["headlines"][:3]))


def equity_curve(mandates):
    if not mandates:
        return
    eq = [m.get("equity", config.INITIAL_BALANCE) for m in mandates]
    st.subheader("Equity curve")
    st.line_chart(pd.DataFrame({"equity": eq}))


def weights_panel():
    st.subheader("Learned strategy weights (per regime)")
    summ = evolution.summary()
    rows = []
    for regime, row in summ["weights"].items():
        entry = {"regime": regime}
        entry.update({k: round(v, 3) for k, v in row.items()})
        rows.append(entry)
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("🛡️ Vault saves (good blocks)", summ["vault_saves_good"])
    c2.metric("⚠️ Bad blocks", summ["vault_saves_bad"])
    c3.metric("⏳ Pending", summ["vault_saves_pending"])


def evolution_log(evo):
    st.subheader("Evolution log")
    if not evo:
        st.caption("No weight changes yet — needs enough closed trades per regime.")
        return
    for e in reversed(evo[-10:]):
        st.markdown(f"- `{e['regime']}` · **{e['agent']}** {e['old_weight']}→{e['new_weight']} — {e['reason']}")


def trade_log():
    st.subheader("Trade log (CSV)")
    if not os.path.exists(config.TRADE_LOG_CSV):
        st.caption("No fills logged yet.")
        return
    df = pd.read_csv(config.TRADE_LOG_CSV)
    st.dataframe(df.tail(50), hide_index=True, use_container_width=True)
    st.download_button("Download trade_log.csv", df.to_csv(index=False),
                       file_name="trade_log.csv", mime="text/csv")


def mandates_table(mandates):
    st.subheader("Mandate ledger")
    if not mandates:
        return
    rows = [{
        "id": m["mandate_id"], "action": m["action"], "regime": m["regime"],
        "conf": m["confidence"], "vault": m.get("vault", {}).get("decision"),
        "equity": m.get("equity"),
    } for m in mandates[-50:]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def prediction_panel():
    """Prediction-market mode view (if it has been run)."""
    pf = store.read_json(config.PRED_PORTFOLIO_FILE, {})
    mandates = store.read_json(config.PRED_MANDATES_FILE, [])
    if not pf and not mandates:
        return
    st.divider()
    st.subheader("🎲 Prediction markets (Polymarket)")
    eq = pf.get("equity", config.PRED_INITIAL_BALANCE)
    c1, c2, c3 = st.columns(3)
    c1.metric("Pred equity", f"${eq:,.2f}", f"{(eq/config.PRED_INITIAL_BALANCE-1)*100:+.2f}%")
    c2.metric("Closed", pf.get("closed_trades", 0))
    c3.metric("Open", len(pf.get("open_positions", [])))
    if mandates:
        rows = [{
            "market": m["market"][:60], "yes": m.get("yes_price"),
            "est": m.get("est_prob"), "edge": m.get("edge"),
            "action": m.get("action"), "decision": m.get("vault", {}).get("decision"),
        } for m in mandates[-12:]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def main():
    with st.sidebar:
        st.header("Controls")
        st.caption(f"Provider: **{config.LLM_PROVIDER}**")
        st.caption(f"Basket: **{', '.join(config.SYMBOL_ALLOWLIST)}**")
        st.caption(f"TF: **{config.LOOP_TIMEFRAME}** · Leverage: **{config.LEVERAGE}x**")
        if st.button("🔄 Refresh"):
            st.rerun()
        auto = st.checkbox("Auto-refresh (5s)", value=False)

    portfolio, mandates, orders, evo, saves = load_all()
    header(portfolio)
    st.divider()
    accountability_hero()
    st.divider()
    basket_panel(mandates)
    st.divider()
    latest_decision(mandates)
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        equity_curve(mandates)
    with col2:
        weights_panel()
    st.divider()
    evolution_log(evo)
    mandates_table(mandates)
    trade_log()
    prediction_panel()

    if auto:
        import time
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
