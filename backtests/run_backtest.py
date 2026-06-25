"""Generate a reproducible VesperClaw backtest report.

This is intentionally lightweight and deterministic. It replays historical
OHLCV candles through the shipped indicator stack, Alpha Gate-style filters,
and ATR stop/target exits. It does not call an LLM and does not place orders.

Usage:
    python backtests/run_backtest.py --limit 1000 --timeframe 15m

Outputs:
    backtests/report.md
    backtests/trades.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from vesperclaw.snapshot import (  # noqa: E402
    _adx,
    _atr,
    _ema,
    _fetch_ohlcv,
    _label_regime,
    _rsi,
    _synthetic_ohlcv,
)


OUT_DIR = ROOT / "backtests"
TRADE_FILE = OUT_DIR / "trades.csv"
REPORT_FILE = OUT_DIR / "report.md"


@dataclass
class OpenTrade:
    symbol: str
    side: str
    entry_time: str
    entry_bar: int
    entry_price: float
    quantity: float
    notional: float
    stop: float
    target: float
    regime: str
    confidence: float


def _load_data(symbol: str, timeframe: str, limit: int, demo_data: bool) -> tuple[pd.DataFrame, str]:
    if demo_data:
        return _synthetic_ohlcv(limit=limit, seed=abs(hash(symbol)) % 9999), "synthetic fallback"
    try:
        return _fetch_ohlcv(symbol, timeframe=timeframe, limit=limit), "Bitget public OHLCV"
    except Exception as exc:  # noqa: BLE001
        df = _synthetic_ohlcv(limit=limit, seed=abs(hash(symbol)) % 9999)
        return df, f"synthetic fallback; Bitget fetch failed: {exc}"


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    close = df["close"]
    adx, plus_di, minus_di = _adx(df, config.ATR_PERIOD)
    atr = _atr(df, config.ATR_PERIOD)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["atr"] = atr
    df["atr_pct"] = atr / close * 100
    df["ema_fast"] = _ema(close, config.EMA_FAST)
    df["ema_slow"] = _ema(close, config.EMA_SLOW)
    df["rsi"] = _rsi(close, config.RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df["recent_return_pct"] = (close / close.shift(5) - 1) * 100
    return df


def _prepare_htf(df: pd.DataFrame) -> pd.DataFrame:
    htf = (
        df.set_index("dt")
        .resample("1h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    if len(htf) < max(80, config.EMA_SLOW + config.ATR_PERIOD):
        return pd.DataFrame()
    htf = _prepare(htf)
    return htf


def _htf_confirms(htf: pd.DataFrame, now: Any, side: str | None) -> bool:
    if not side or htf.empty:
        return False
    rows = htf[htf["dt"] <= now]
    if len(rows) < max(80, config.EMA_SLOW + config.ATR_PERIOD):
        return False
    row = rows.iloc[-1]
    regime, _ = _label_regime(float(row.adx), float(row.plus_di), float(row.minus_di))
    if side == "long":
        return regime == "trend_up" and float(row.ema_fast) > float(row.ema_slow)
    return regime == "trend_down" and float(row.ema_fast) < float(row.ema_slow)


def _confidence(row: pd.Series) -> float:
    _, regime_conf = _label_regime(float(row.adx), float(row.plus_di), float(row.minus_di))
    adx_bonus = max(0.0, min(0.18, (float(row.adx) - config.MIN_TREND_ADX) / 100))
    volume_bonus = 0.04 if float(row.volume) >= float(row.vol_avg or 0) else 0.0
    return round(min(0.95, 0.58 + regime_conf * 0.22 + adx_bonus + volume_bonus), 3)


def _signal(row: pd.Series, htf: pd.DataFrame) -> tuple[str | None, str, list[str], float]:
    regime, _ = _label_regime(float(row.adx), float(row.plus_di), float(row.minus_di))
    side: str | None = None
    if regime == "trend_up" and float(row.ema_fast) > float(row.ema_slow):
        side = "long"
    elif regime == "trend_down" and float(row.ema_fast) < float(row.ema_slow):
        side = "short"

    reasons: list[str] = []
    if regime not in ("trend_up", "trend_down"):
        reasons.append(f"{regime} regime")
    if float(row.adx) < config.MIN_TREND_ADX:
        reasons.append(f"ADX {float(row.adx):.2f} < {config.MIN_TREND_ADX:g}")
    if float(row.volume) < float(row.vol_avg or 0) * 0.9:
        reasons.append("volume falling")
    if side == "long" and float(row.rsi) >= 72:
        reasons.append("RSI overextended for long")
    if side == "short" and float(row.rsi) <= 28:
        reasons.append("RSI overextended for short")
    if side == "long" and float(row.recent_return_pct or 0) < -0.05:
        reasons.append("recent return fights long")
    if side == "short" and float(row.recent_return_pct or 0) > 0.05:
        reasons.append("recent return fights short")
    if config.REQUIRE_HTF_CONFIRMATION and not _htf_confirms(htf, row["dt"], side):
        reasons.append(f"{config.HTF_TIMEFRAME} does not confirm")

    conf = _confidence(row)
    if conf < config.MIN_CONFIDENCE:
        reasons.append(f"confidence {conf:.2f} < {config.MIN_CONFIDENCE:.2f}")

    return side if not reasons else None, regime, reasons, conf


def _close_trade(trade: OpenTrade, row: pd.Series, reason: str, equity: float) -> tuple[dict[str, Any], float]:
    exit_price = float(row.close)
    if reason == "STOP":
        exit_price = trade.stop
    elif reason == "TARGET":
        exit_price = trade.target

    gross = (
        (exit_price - trade.entry_price) * trade.quantity
        if trade.side == "long"
        else (trade.entry_price - exit_price) * trade.quantity
    )
    fee = (trade.notional + abs(exit_price * trade.quantity)) * config.TAKER_FEE
    pnl = gross - fee
    balance_after = equity + pnl
    record = {
        **asdict(trade),
        "exit_time": str(row["dt"]),
        "exit_price": round(exit_price, 6),
        "reason": reason,
        "fee": round(fee, 4),
        "pnl": round(pnl, 4),
        "balance_before": round(equity, 4),
        "balance_after": round(balance_after, 4),
        "return_pct": round((pnl / equity) * 100, 4) if equity else 0.0,
    }
    return record, balance_after


def run_symbol(symbol: str, timeframe: str, limit: int, demo_data: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw, source = _load_data(symbol, timeframe, limit, demo_data)
    df = _prepare(raw)
    equity = config.INITIAL_BALANCE
    peak = equity
    max_drawdown = 0.0
    open_trade: OpenTrade | None = None
    trades: list[dict[str, Any]] = []
    rejected = 0
    cooldown_until = -1

    warmup = max(80, config.BB_PERIOD + config.ATR_PERIOD + config.EMA_SLOW)
    htf = _prepare_htf(raw)
    for i in range(warmup, len(df)):
        row = df.iloc[i]

        if open_trade:
            stop_hit = float(row.low) <= open_trade.stop if open_trade.side == "long" else float(row.high) >= open_trade.stop
            target_hit = float(row.high) >= open_trade.target if open_trade.side == "long" else float(row.low) <= open_trade.target
            timed_out = i - open_trade.entry_bar >= config.TIMEOUT_BARS
            reason = "STOP" if stop_hit else "TARGET" if target_hit else "TIMEOUT" if timed_out else None
            if reason:
                rec, equity = _close_trade(open_trade, row, reason, equity)
                trades.append(rec)
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)
                open_trade = None
                cooldown_until = i + config.COOLDOWN_BARS
            continue

        if i < cooldown_until:
            rejected += 1
            continue

        side, regime, reasons, conf = _signal(row, htf)
        if not side:
            rejected += 1
            continue

        price = float(row.close)
        atr = float(row.atr)
        if not math.isfinite(atr) or atr <= 0:
            rejected += 1
            continue

        margin = equity * min(config.MAX_POSITION_SIZE_PCT, 0.04)
        notional = margin * max(1.0, float(config.LEVERAGE))
        quantity = notional / price
        stop = price - config.SL_ATR_MULT * atr if side == "long" else price + config.SL_ATR_MULT * atr
        target = price + config.TP_ATR_MULT * atr if side == "long" else price - config.TP_ATR_MULT * atr
        open_trade = OpenTrade(
            symbol=symbol,
            side=side,
            entry_time=str(row["dt"]),
            entry_bar=i,
            entry_price=round(price, 6),
            quantity=round(quantity, 8),
            notional=round(notional, 4),
            stop=round(stop, 6),
            target=round(target, 6),
            regime=regime,
            confidence=conf,
        )

    if open_trade:
        rec, equity = _close_trade(open_trade, df.iloc[-1], "END_OF_TEST", equity)
        trades.append(rec)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)

    summary = _summary(symbol, source, df, trades, rejected, equity, max_drawdown)
    return trades, summary


def _summary(
    symbol: str,
    source: str,
    df: pd.DataFrame,
    trades: list[dict[str, Any]],
    rejected: int,
    equity: float,
    max_drawdown: float,
) -> dict[str, Any]:
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    pnl = equity - config.INITIAL_BALANCE
    return {
        "symbol": symbol,
        "source": source,
        "candles": len(df),
        "from": str(df["dt"].iloc[0]),
        "to": str(df["dt"].iloc[-1]),
        "closed_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "pnl": round(pnl, 4),
        "return_pct": round(pnl / config.INITIAL_BALANCE * 100, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
        "rejected_setups": rejected,
    }


def write_report(summaries: list[dict[str, Any]], trades: list[dict[str, Any]], args: argparse.Namespace) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(trades).to_csv(TRADE_FILE, index=False)
    total_pnl = sum(s["pnl"] for s in summaries)
    total_trades = sum(s["closed_trades"] for s in summaries)
    total_wins = sum(s["wins"] for s in summaries)
    win_rate = total_wins / total_trades * 100 if total_trades else 0.0
    max_dd = max((s["max_drawdown_pct"] for s in summaries), default=0.0)

    lines = [
        "# VesperClaw Backtest Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report is generated by `backtests/run_backtest.py`. It replays historical candles through a deterministic version of VesperClaw's shipped rule stack: indicators, Alpha Gate-style filters, ATR stops/targets, fees, and paper sizing. It does not call an LLM and does not place real orders.",
        "",
        "## Settings",
        "",
        f"- Symbols: {', '.join(args.symbols)}",
        f"- Timeframe: `{args.timeframe}`",
        f"- Candle limit per symbol: `{args.limit}`",
        f"- Initial balance per symbol: `${config.INITIAL_BALANCE:,.2f}`",
        f"- Position sizing: `min(MAX_POSITION_SIZE_PCT, 4%) * leverage`",
        f"- Stop / target: `{config.SL_ATR_MULT}x ATR / {config.TP_ATR_MULT}x ATR`",
        f"- Fee model: taker fee `{config.TAKER_FEE}` on entry and exit notional",
        "",
        "## Portfolio Summary",
        "",
        f"- Closed trades: `{total_trades}`",
        f"- Win rate: `{win_rate:.2f}%`",
        f"- Aggregate PnL: `${total_pnl:,.2f}`",
        f"- Worst symbol drawdown: `{max_dd:.2f}%`",
        "",
        "## Per-Symbol Results",
        "",
        "| Symbol | Source | Candles | Closed | Win Rate | PnL | Return | Max DD | Rejected |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['symbol']} | {s['source']} | {s['candles']} | {s['closed_trades']} | "
            f"{s['win_rate']:.2f}% | ${s['pnl']:,.2f} | {s['return_pct']:.2f}% | "
            f"{s['max_drawdown_pct']:.2f}% | {s['rejected_setups']} |"
        )
    lines.extend(
        [
            "",
            "## Raw Trades",
            "",
            "The full trade list is in [`trades.csv`](trades.csv).",
            "",
            "## How To Reproduce",
            "",
            "```bash",
            "python backtests/run_backtest.py --limit 1000 --timeframe 15m",
            "```",
            "",
            "For a fully offline reproducible run, use:",
            "",
            "```bash",
            "python backtests/run_backtest.py --demo-data --limit 1000 --timeframe 15m",
            "```",
            "",
            "## Notes",
            "",
            "- This is supplementary, not the live paper log required by the hackathon.",
            "- The live submission record remains `samples/trade_log.csv` and the public dashboard.",
            "- This script is intentionally conservative and transparent so reviewers can inspect or modify the assumptions.",
        ]
    )
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VesperClaw deterministic backtest.")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--demo-data", action="store_true", help="Use synthetic candles instead of Bitget public data.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_trades: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for symbol in args.symbols:
        trades, summary = run_symbol(symbol, args.timeframe, args.limit, args.demo_data)
        all_trades.extend(trades)
        summaries.append(summary)
    write_report(summaries, all_trades, args)
    print(f"Wrote {REPORT_FILE}")
    print(f"Wrote {TRADE_FILE}")


if __name__ == "__main__":
    main()
