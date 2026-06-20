"""Market Snapshot + Regime Referee.

Pulls OHLCV from Bitget (via ccxt, public — no keys needed), computes the
indicator set, and labels the market regime. The regime is the *arbiter* that
decides which strategy agent is allowed to lead:

    ADX >= 25            -> trend_up / trend_down  (Trend Agent priority)
    ADX <= 20            -> range                  (Mean-Reversion Agent priority)
    20 < ADX < 25        -> uncertain              (needs higher confidence / no trade)
    ATR% >= danger       -> Risk Agent can veto everything downstream

When the live feed is unavailable (or DEMO_DATA=true) it falls back to a
synthetic-but-realistic candle generator so the loop is always runnable offline.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

import config


@dataclass
class Snapshot:
    timestamp: str
    symbol: str
    timeframe: str
    price: float
    # indicators
    adx: float
    plus_di: float
    minus_di: float
    ema_fast: float
    ema_slow: float
    rsi: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    atr: float
    atr_pct: float
    volume: float
    volume_state: str
    recent_return_pct: float
    # derived
    regime: str
    regime_confidence: float
    high_volatility: bool
    # microstructure (optional; None if unavailable)
    funding_rate: float | None = None
    long_short_ratio: float | None = None
    open_interest: float | None = None
    # raw signal flags computed deterministically (ground truth for agents)
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── indicator math (no external TA dependency for the core; uses pandas/numpy) ──

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (adx, +DI, -DI)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


# ── data acquisition ───────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    """Fetch candles from Bitget public API via ccxt."""
    import ccxt

    exchange = ccxt.bitget({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def _fetch_microstructure(symbol: str) -> dict[str, float | None]:
    """Best-effort funding / OI / long-short. Returns None values on failure."""
    out: dict[str, float | None] = {
        "funding_rate": None,
        "open_interest": None,
        "long_short_ratio": None,
    }
    try:
        import ccxt

        ex = ccxt.bitget({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        swap_symbol = symbol if ":" in symbol else f"{symbol}:USDT"
        try:
            fr = ex.fetch_funding_rate(swap_symbol)
            out["funding_rate"] = fr.get("fundingRate")
        except Exception:  # noqa: BLE001
            pass
        try:
            oi = ex.fetch_open_interest(swap_symbol)
            out["open_interest"] = oi.get("openInterestAmount") or oi.get("openInterestValue")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"microstructure fetch skipped: {e}")
    return out


def _synthetic_ohlcv(limit: int = 200, seed: int | None = None) -> pd.DataFrame:
    """Generate realistic candles (regime-switching random walk) for offline runs."""
    rng = np.random.default_rng(seed)
    n = limit
    price = 67000.0
    closes = []
    # alternate trend / chop segments so regimes actually occur
    drift_schedule = rng.choice([0.0008, -0.0008, 0.0, 0.0], size=n // 25 + 1)
    for i in range(n):
        drift = drift_schedule[i // 25]
        vol = 0.004 if drift != 0 else 0.0015
        price *= 1 + rng.normal(drift, vol)
        closes.append(price)
    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0, 0.0015, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.0015, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    vols = rng.uniform(50, 200, n)
    ts = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="1min")
    return pd.DataFrame(
        {
            "ts": (ts.astype("int64") // 10**6),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": vols,
            "dt": ts,
        }
    )


# ── regime labelling ────────────────────────────────────────────────────

def _label_regime(adx: float, plus_di: float, minus_di: float) -> tuple[str, float]:
    """Return (regime, confidence in [0,1])."""
    if adx >= config.ADX_TREND_MIN:
        regime = "trend_up" if plus_di >= minus_di else "trend_down"
        # confidence scales with how far past the threshold ADX is
        conf = min(1.0, 0.5 + (adx - config.ADX_TREND_MIN) / 50.0)
        return regime, round(conf, 3)
    if adx <= config.ADX_RANGE_MAX:
        conf = min(1.0, 0.5 + (config.ADX_RANGE_MAX - adx) / 40.0)
        return "range", round(conf, 3)
    return "uncertain", 0.35


def _compute_signals(df: pd.DataFrame, snap_vals: dict[str, float]) -> dict[str, Any]:
    """Deterministic strategy signals — the ground truth agents reason over."""
    close = df["close"]
    ema_fast_series = _ema(close, config.EMA_FAST)
    ema_slow_series = _ema(close, config.EMA_SLOW)

    # EMA crossover (trend)
    cross_up = ema_fast_series.iloc[-2] <= ema_slow_series.iloc[-2] and \
        ema_fast_series.iloc[-1] > ema_slow_series.iloc[-1]
    cross_down = ema_fast_series.iloc[-2] >= ema_slow_series.iloc[-2] and \
        ema_fast_series.iloc[-1] < ema_slow_series.iloc[-1]
    ema_long = ema_fast_series.iloc[-1] > ema_slow_series.iloc[-1]

    # RSI + Bollinger (mean reversion)
    price = snap_vals["price"]
    rsi = snap_vals["rsi"]
    at_lower = price <= snap_vals["bb_lower"]
    at_upper = price >= snap_vals["bb_upper"]

    return {
        "ema_cross_up": bool(cross_up),
        "ema_cross_down": bool(cross_down),
        "ema_long_bias": bool(ema_long),
        "rsi_oversold": bool(rsi <= config.RSI_OVERSOLD),
        "rsi_overbought": bool(rsi >= config.RSI_OVERBOUGHT),
        "price_at_lower_bb": bool(at_lower),
        "price_at_upper_bb": bool(at_upper),
        # convenience pre-computed entries per strategy
        "trend_entry": "long" if cross_up else ("short" if cross_down else None),
        "reversion_entry": (
            "long" if (rsi <= config.RSI_OVERSOLD and at_lower)
            else ("short" if (rsi >= config.RSI_OVERBOUGHT and at_upper) else None)
        ),
    }


def build_snapshot(symbol: str | None = None, timeframe: str | None = None,
                   df: pd.DataFrame | None = None) -> Snapshot:
    """Build a full market snapshot. Pass `df` to drive replay/backtest."""
    symbol = symbol or config.SYMBOL
    timeframe = timeframe or config.LOOP_TIMEFRAME

    if df is None:
        if config.DEMO_DATA:
            df = _synthetic_ohlcv()
        else:
            try:
                df = _fetch_ohlcv(symbol, timeframe)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Live fetch failed ({e}); falling back to synthetic data.")
                df = _synthetic_ohlcv()

    close = df["close"]
    adx_s, plus_di_s, minus_di_s = _adx(df, config.ATR_PERIOD)
    ema_fast_s = _ema(close, config.EMA_FAST)
    ema_slow_s = _ema(close, config.EMA_SLOW)
    rsi_s = _rsi(close, config.RSI_PERIOD)
    bb_mid_s = close.rolling(config.BB_PERIOD).mean()
    bb_std_s = close.rolling(config.BB_PERIOD).std(ddof=0)
    atr_s = _atr(df, config.ATR_PERIOD)

    price = float(close.iloc[-1])
    atr = float(atr_s.iloc[-1])
    atr_pct = round(atr / price * 100, 3) if price else 0.0
    adx = round(float(adx_s.iloc[-1]), 2)
    plus_di = round(float(plus_di_s.iloc[-1]), 2)
    minus_di = round(float(minus_di_s.iloc[-1]), 2)

    bb_mid = float(bb_mid_s.iloc[-1])
    bb_std = float(bb_std_s.iloc[-1]) if not np.isnan(bb_std_s.iloc[-1]) else 0.0

    vol_now = float(df["volume"].iloc[-1])
    vol_avg = float(df["volume"].tail(20).mean())
    volume_state = "rising" if vol_now > vol_avg * 1.1 else ("falling" if vol_now < vol_avg * 0.9 else "flat")
    recent_return = round((price / float(close.iloc[-6]) - 1) * 100, 3) if len(close) > 6 else 0.0

    regime, regime_conf = _label_regime(adx, plus_di, minus_di)
    high_vol = atr_pct >= config.DANGER_VOLATILITY_PCT

    snap_vals = {
        "price": price,
        "rsi": round(float(rsi_s.iloc[-1]), 2),
        "bb_lower": bb_mid - config.BB_STD * bb_std,
        "bb_upper": bb_mid + config.BB_STD * bb_std,
    }
    signals = _compute_signals(df, snap_vals)

    micro = {"funding_rate": None, "open_interest": None, "long_short_ratio": None}
    if df is None or not config.DEMO_DATA:
        try:
            micro = _fetch_microstructure(symbol)
        except Exception:  # noqa: BLE001
            pass

    return Snapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        timeframe=timeframe,
        price=round(price, 2),
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        ema_fast=round(float(ema_fast_s.iloc[-1]), 2),
        ema_slow=round(float(ema_slow_s.iloc[-1]), 2),
        rsi=snap_vals["rsi"],
        bb_upper=round(snap_vals["bb_upper"], 2),
        bb_mid=round(bb_mid, 2),
        bb_lower=round(snap_vals["bb_lower"], 2),
        atr=round(atr, 2),
        atr_pct=atr_pct,
        volume=round(vol_now, 2),
        volume_state=volume_state,
        recent_return_pct=recent_return,
        regime=regime,
        regime_confidence=regime_conf,
        high_volatility=high_vol,
        funding_rate=micro.get("funding_rate"),
        long_short_ratio=micro.get("long_short_ratio"),
        open_interest=micro.get("open_interest"),
        signals=signals,
    )


if __name__ == "__main__":
    # quick manual check
    config.DEMO_DATA = True
    snap = build_snapshot()
    import json

    print(json.dumps(snap.to_dict(), indent=2, default=str))
