"""VesperClaw orchestrator — the autonomous loop (multi-asset).

Each cycle the agent scans every symbol in the basket (SYMBOL_ALLOWLIST):

    for each symbol:
        snapshot -> regime referee -> council (debate) -> mandate
            -> AgentVault (per-symbol + portfolio limits) -> paper execution
    then: resolve exits, reconcile vault saves, mark portfolio, evolve

Run modes:
    live_paper  : one full basket scan every LOOP_INTERVAL_SECONDS (15m candles).
    fast_demo   : replay 1-minute candles quickly across the basket so a full loop
                  (entries, exits, evolution, vault saves) is visible in minutes.

Usage:
    python main.py --mode fast_demo --reset
    python main.py --mode live_paper
    python main.py --symbols BTC/USDT,ETH/USDT
    python main.py --reset
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
_ACTIVE_PROFILE_SIG = ""
_PRED_ENGINE = None


def _apply_saved_profile_if_changed() -> None:
    """Hot-load the natural-language contract profile between cycles."""
    global _ACTIVE_PROFILE_SIG
    from vesperclaw import vibe

    profile = vibe.load_profile()
    sig = repr(sorted((k, repr(v)) for k, v in profile.items()))
    if sig == _ACTIVE_PROFILE_SIG:
        return
    _ACTIVE_PROFILE_SIG = sig
    if profile:
        vibe.apply_profile(profile)


def run_cycle(engine: PaperEngine, symbols: list[str],
              frame_map: dict[str, pd.DataFrame] | None = None) -> list[dict[str, Any]]:
    """Execute one autonomous cycle across the whole basket."""
    _apply_saved_profile_if_changed()
    engine.begin_cycle()  # advance cycle + daily rollover; prices marked after scan
    price_map: dict[str, float] = {}
    records: list[dict[str, Any]] = []
    actions: list[str] = []

    for sym in symbols:
        df = frame_map.get(sym) if frame_map else None
        snap = build_snapshot(symbol=sym, df=df)
        price_map[sym] = snap.price
        engine.update_price(sym, snap.price)

        # resolve this symbol's open trades before considering a new entry
        for closed in engine.check_exits(snap.price, symbol=sym):
            evolution.update_from_close(closed)

        weights = evolution.weights_for(snap.regime)
        council = run_council(snap, weights)
        seq = engine.next_seq()
        mandate = build_mandate(snap, council, seq)

        vault = vault_evaluate(mandate, engine.portfolio_view(symbol=sym))
        if vault.execution_allowed:
            engine.open_position(mandate, vault, snap.price)

        record = mandate.to_dict()
        record["vault"] = vault.to_dict()
        record["equity"] = engine.state["equity"]
        store.append_json_list(config.MANDATES_FILE, record, cap=1500)
        records.append(record)
        if mandate.action != "NO_TRADE":
            actions.append(f"{sym.split('/')[0]}:{mandate.action}/{vault.decision}")

    # portfolio-level housekeeping
    evolution.reconcile_vault_saves(price_map)
    engine.mark_prices(price_map)
    engine.save()
    try:
        from vesperclaw import agent_hub
        agent_hub.write_status()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"agent hub status skipped: {e}")
    try:
        from vesperclaw import loop_state
        loop_state.write_loop_state()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"loop state skipped: {e}")

    if (
        config.PRED_RUN_IN_MAIN_LOOP
        and config.PRED_RUN_EVERY_CYCLES > 0
        and engine.state["cycle"] % config.PRED_RUN_EVERY_CYCLES == 0
    ):
        _run_prediction_cycle_safely()

    # refresh the accountability briefing periodically (keeps LLM cost low)
    if engine.state["cycle"] % config.BRIEFING_EVERY_CYCLES == 0:
        try:
            from vesperclaw import briefing
            briefing.write_briefing()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"briefing skipped: {e}")

    logger.info(
        f"cycle {engine.state['cycle']} | "
        f"{', '.join(actions) if actions else 'no entries'} | "
        f"open={len(engine.state['open_positions'])} equity={engine.state['equity']}"
    )
    return records


def _run_prediction_cycle_safely() -> None:
    """Refresh prediction markets without letting that lane stop crypto scanning."""
    global _PRED_ENGINE
    try:
        from vesperclaw import prediction

        if _PRED_ENGINE is None:
            _PRED_ENGINE = prediction.PredEngine()
        prediction.run_cycle(_PRED_ENGINE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"prediction refresh skipped: {e}")


# ── fast-demo replay ────────────────────────────────────────────────────

def _symbol_series(symbol: str) -> pd.DataFrame:
    from vesperclaw.snapshot import _fetch_ohlcv, _synthetic_ohlcv

    if config.DEMO_DATA:
        return _synthetic_ohlcv(limit=800, seed=abs(hash(symbol)) % 9999)
    try:
        df = _fetch_ohlcv(symbol, "1m", limit=1000)
        logger.info(f"{symbol}: replaying {len(df)} live 1m candles.")
        return df
    except Exception as e:  # noqa: BLE001
        logger.warning(f"{symbol}: live fetch failed ({e}); using synthetic candles.")
        return _synthetic_ohlcv(limit=800, seed=abs(hash(symbol)) % 9999)


def _replay_frame_maps(symbols: list[str]) -> list[dict[str, pd.DataFrame]]:
    """Build per-cycle {symbol: window} maps, aligned to the shortest series."""
    series = {s: _symbol_series(s) for s in symbols}
    n = min(len(df) for df in series.values())
    frame_maps: list[dict[str, pd.DataFrame]] = []
    for end in range(MIN_WINDOW, n):
        frame_maps.append({s: series[s].iloc[:end].reset_index(drop=True) for s in symbols})
    return frame_maps


def run_fast_demo(engine: PaperEngine, symbols: list[str], max_cycles: int | None) -> None:
    frame_maps = _replay_frame_maps(symbols)
    if max_cycles:
        frame_maps = frame_maps[:max_cycles]
    logger.info(f"FAST_DEMO: {len(frame_maps)} cycles over {len(symbols)} symbols "
                f"(interval {config.LOOP_INTERVAL_SECONDS}s)")
    for fm in frame_maps:
        run_cycle(engine, symbols, fm)
        time.sleep(config.LOOP_INTERVAL_SECONDS)
    _print_summary(engine)


def run_live_paper(engine: PaperEngine, symbols: list[str], max_cycles: int | None) -> None:
    logger.info(f"LIVE_PAPER: basket {symbols} every {config.LOOP_INTERVAL_SECONDS}s "
                f"on {config.LOOP_TIMEFRAME}")
    count = 0
    while True:
        run_cycle(engine, symbols)
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
                 config.TRADE_LOG_CSV, config.PRED_PORTFOLIO_FILE,
                 config.PRED_MANDATES_FILE, config.PRED_ORDERS_FILE,
                 config.PRED_TRADE_LOG_CSV, config.LOOP_STATE_FILE):
        if os.path.exists(path):
            os.remove(path)
    logger.info("State reset.")


def main() -> None:
    parser = argparse.ArgumentParser(description="VesperClaw autonomous paper-trading agent")
    parser.add_argument("--mode", choices=["live_paper", "fast_demo", "prediction"],
                        default=config.RUN_MODE)
    parser.add_argument("--symbols", type=str, default=None,
                        help="comma-separated basket (default: SYMBOL_ALLOWLIST)")
    parser.add_argument("--cycles", type=int, default=None, help="max cycles")
    parser.add_argument("--reset", action="store_true", help="wipe state before running")
    parser.add_argument("--demo-data", action="store_true", help="use synthetic candles offline")
    parser.add_argument("--vibe", type=str, default=None,
                        help='set a natural-language trading style, e.g. "aggressive trend follower, BTC+ETH, 3x"')
    args = parser.parse_args()

    if args.demo_data:
        config.DEMO_DATA = True
    if args.reset:
        _reset_state()

    # Natural-language "vibe" overrides: compile a new one, else apply any saved profile.
    from vesperclaw import vibe
    if args.vibe:
        vibe.set_vibe(args.vibe)
    else:
        vibe.apply_profile(vibe.load_profile())

    # Prediction-market mode is a self-contained second instrument class.
    if args.mode == "prediction":
        from vesperclaw import prediction
        prediction.run(cycles=args.cycles, interval=config.LOOP_INTERVAL_SECONDS)
        return

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else config.SYMBOL_ALLOWLIST

    store.ensure_dirs()
    engine = PaperEngine()
    logger.info(f"VesperClaw starting | provider={config.LLM_PROVIDER} basket={symbols} "
                f"mode={args.mode} demo_data={config.DEMO_DATA}")

    if args.mode == "fast_demo":
        run_fast_demo(engine, symbols, args.cycles)
    else:
        run_live_paper(engine, symbols, args.cycles)


if __name__ == "__main__":
    main()
