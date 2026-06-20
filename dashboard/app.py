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
from vesperclaw import store, evolution  # noqa: E402

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
        }, expanded=False)


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


def main():
    with st.sidebar:
        st.header("Controls")
        st.caption(f"Provider: **{config.LLM_PROVIDER}**")
        st.caption(f"Symbol: **{config.SYMBOL}** · TF: **{config.LOOP_TIMEFRAME}**")
        if st.button("🔄 Refresh"):
            st.rerun()
        auto = st.checkbox("Auto-refresh (5s)", value=False)

    portfolio, mandates, orders, evo, saves = load_all()
    header(portfolio)
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

    if auto:
        import time
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
