"""On-chain perception (best-effort, keyless).

Uses DeFiLlama's public API (no key) to read aggregate DeFi TVL as a market-wide
risk-on/off proxy: rising TVL = capital flowing into crypto (risk appetite up),
falling TVL = risk-off. This is a *macro* signal, not per-coin — it nudges the
agent's bias, it doesn't drive entries.

Everything is cached and wrapped in try/except so a flaky/3rd-party outage never
breaks the trading loop (the fields simply come back None).
"""
from __future__ import annotations

import time
from typing import Any

import requests
from loguru import logger

_TVL_CACHE: dict[str, Any] = {"ts": 0, "change_7d": None, "tvl": None}
_TVL_TTL = 1800  # 30 min — TVL moves slowly


def fetch_tvl_trend() -> dict[str, Any]:
    """Return {'tvl': float|None, 'change_7d': float|None} for total DeFi TVL."""
    now = time.time()
    if now - _TVL_CACHE["ts"] < _TVL_TTL and _TVL_CACHE["tvl"] is not None:
        return {"tvl": _TVL_CACHE["tvl"], "change_7d": _TVL_CACHE["change_7d"]}
    try:
        r = requests.get("https://api.llama.fi/v2/historicalChainTvl", timeout=10)
        series = r.json()  # list of {date, tvl}
        if not series or len(series) < 8:
            return {"tvl": None, "change_7d": None}
        latest = float(series[-1]["tvl"])
        week_ago = float(series[-8]["tvl"])
        change = (latest - week_ago) / week_ago * 100 if week_ago else 0.0
        _TVL_CACHE.update(ts=now, tvl=latest, change_7d=round(change, 2))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"DeFiLlama TVL fetch failed: {e}")
        return {"tvl": None, "change_7d": None}
    return {"tvl": _TVL_CACHE["tvl"], "change_7d": _TVL_CACHE["change_7d"]}


def get_onchain() -> dict[str, Any]:
    """Compact on-chain signal for the snapshot."""
    tvl = fetch_tvl_trend()
    change = tvl["change_7d"]
    if change is None:
        regime = None
    elif change >= 2:
        regime = "risk_on"
    elif change <= -2:
        regime = "risk_off"
    else:
        regime = "neutral"
    return {"defi_tvl": tvl["tvl"], "defi_tvl_change_7d": change, "onchain_regime": regime}


if __name__ == "__main__":
    import json
    print(json.dumps(get_onchain(), indent=2))
