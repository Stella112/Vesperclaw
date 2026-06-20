"""VesperClaw orchestrator — the autonomous loop.

Wires every component into one cycle:

    snapshot -> regime referee -> council (debate) -> mandate
        -> AgentVault -> paper execution -> close (TP/SL/timeout) -> evolution

Run modes:
    live_paper  : one cycle every LOOP_INTERVAL_SECONDS using the latest candle.
    fast_demo   : replay 1-minute candles quickly so a full loop (entries, exits,
                  evolution, vault saves) is visible in a 2-3 minute demo.

Usage:
    python main.py                      # uses RUN_MODE from .env
    python main.py --mode fast_demo --cycles 300
    python main.py --mode live_paper
    python main.py --reset              # wipe state and start fresh
"""
from __future__ import annotations

import argparse
import os
import time
from typing import Any

import pandas as pd
from loguru import logger

import config
from vesperclaw import evolution, store
from vesperclaw.agents import run_council
from vesperclaw.mandate import build_mandate
from vesperclaw.paper_engine import PaperEngine
from vesperclaw.snapshot import build_snapshot
from vesperclaw.vault import evaluate as vault_evaluate

MIN_WINDOW = max(config.BB_PERIOD, config.EMA_SLOW, config.ATR_PERIOD) + 5


def run_cycle(engine: PaperEngine, df_window: pd.DataFrame | None = None) -> dict[str, Any]:
    """Execute a single autonomous cycle and persist all artifacts."""
    snap = build_snapshot(df=df_window)
    engine.begin_cycle(snap.price)

    # 1. resolve open trades first (so capital/positions free up before new entries)
    for closed in engine.check_exits(snap.price):
        evolution.update_from_close(closed)
    evolution.reconcile_vault_saves(snap.price)

    # 2. council reasons with the learned weights for THIS regime
    weights = evolution.weights_for(snap.regime)
    council = run_council(snap, weights)

    # 3. build the mandate (recorded even when it's a NO_TRADE / refusal)
    seq = engine.next_seq()
    mandate = build_mandate(snap, council, seq)

    # 4. firewall + execution
    vault = vault_evaluate(mandate, engine.portfolio_view())
    if vault.execution_allowed:
        engine.open_position(mandate, vault, snap.price)

    # 5. persist the full decision record for the audit trail / dashboard
    record = mandate.to_dict()
    record["vault"] = vault.to_dict()
    record["equity"] = engine.state["equity"]
    store.append_json_list(config.MANDATES_FILE, record, cap=1000)
    engine.save()

    logger.info(
        f"cycle {engine.state['cycle']} | regime={snap.regime} "
        f"action={mandate.action} vault={vault.decision} "
        f"equity={engine.state['equity']}"
    )
    return record


def _replay_frames(symbol: str) -> list[pd.DataFrame]:
    """Build a list of sliding candle windows for fast-demo replay."""
    from vesperclaw.snapshot import _fetch_ohlcv, _synthetic_ohlcv

    if config.DEMO_DATA:
        df = _synthetic_ohlcv(limit=800, seed=7)
    else:
        try:
            df = _fetch_ohlcv(symbol, "1m", limit=1000)
            logger.info(f"Replaying {len(df)} live 1m candles from Bitget.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Live fetch failed ({e}); using synthetic candles.")
            df = _synthetic_ohlcv(limit=800, seed=7)

    frames = []
    for end in range(MIN_WINDOW, len(df)):
        frames.append(df.iloc[:end].reset_index(drop=True))
    return frames


def run_fast_demo(engine: PaperEngine, max_cycles: int | None) -> None:
    frames = _replay_frames(config.SYMBOL)
    if max_cycles:
        frames = frames[:max_cycles]
    logger.info(f"FAST_DEMO: replaying {len(frames)} cycles (interval {config.LOOP_INTERVAL_SECONDS}s)")
    for frame in frames:
        run_cycle(engine, df_window=frame)
        time.sleep(config.LOOP_INTERVAL_SECONDS)
    _print_summary(engine)


def run_live_paper(engine: PaperEngine, max_cycles: int | None) -> None:
    logger.info(f"LIVE_PAPER: cycle every {config.LOOP_INTERVAL_SECONDS}s on {config.LOOP_TIMEFRAME}")
    count = 0
    while True:
        run_cycle(engine)
        count += 1
        if max_cycles and count >= max_cycles:
            break
        time.sleep(config.LOOP_INTERVAL_SECONDS)
    _print_summary(engine)


def _print_summary(engine: PaperEngine) -> None:
    s = engine.state
    closed = s["closed_trades"]
    wr = (s["wins"] / closed * 100) if closed else 0.0
    ret = (s["equity"] / config.INITIAL_BALANCE - 1) * 100
    evo = evolution.summary()
    logger.info("─" * 60)
    logger.info(f"SUMMARY  equity={s['equity']}  return={ret:+.2f}%  "
                f"trades={closed}  win_rate={wr:.1f}%")
    logger.info(f"Vault saves: good={evo['vault_saves_good']} bad={evo['vault_saves_bad']} "
                f"pending={evo['vault_saves_pending']}")
    logger.info("─" * 60)


def _reset_state() -> None:
    for path in (config.PORTFOLIO_FILE, config.MANDATES_FILE, config.ORDERS_FILE,
                 config.EVOLUTION_FILE, config.WEIGHTS_FILE, config.VAULT_SAVES_FILE,
                 config.TRADE_LOG_CSV):
        if os.path.exists(path):
            os.remove(path)
    logger.info("State reset.")


def main() -> None:
    parser = argparse.ArgumentParser(description="VesperClaw autonomous paper-trading agent")
    parser.add_argument("--mode", choices=["live_paper", "fast_demo"], default=config.RUN_MODE)
    parser.add_argument("--cycles", type=int, default=None, help="max cycles (default: unlimited/all)")
    parser.add_argument("--reset", action="store_true", help="wipe state before running")
    parser.add_argument("--demo-data", action="store_true", help="use synthetic candles offline")
    args = parser.parse_args()

    if args.demo_data:
        config.DEMO_DATA = True
    if args.reset:
        _reset_state()

    store.ensure_dirs()
    engine = PaperEngine()
    logger.info(f"VesperClaw starting | provider={config.LLM_PROVIDER} symbol={config.SYMBOL} "
                f"mode={args.mode} demo_data={config.DEMO_DATA}")

    if args.mode == "fast_demo":
        run_fast_demo(engine, args.cycles)
    else:
        run_live_paper(engine, args.cycles)


if __name__ == "__main__":
    main()
