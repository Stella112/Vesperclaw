"""Meme Radar.

User-facing scanner for meme coins. It pulls public market/trending data,
scores the candidate with transparent risk rules, and returns a BUY/WATCH/AVOID
verdict. This module never places orders; it is an analysis gate for the
dashboard and submission demo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

import config
from vesperclaw import store


MEME_WORDS = {
    "ai",
    "bonk",
    "cat",
    "dog",
    "doge",
    "floki",
    "frog",
    "inu",
    "meme",
    "mog",
    "pepe",
    "ponke",
    "popcat",
    "pudgy",
    "shib",
    "wif",
}


def _headers() -> dict[str, str]:
    if not config.MEME_RADAR_API_KEY:
        return {}
    key_name = "x-cg-pro-api-key" if "pro-api" in config.MEME_RADAR_API_BASE else "x-cg-demo-api-key"
    return {key_name: config.MEME_RADAR_API_KEY}


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{config.MEME_RADAR_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    response = requests.get(url, params=params or {}, headers=_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_market(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "symbol": str(row.get("symbol", "")).upper(),
        "name": row.get("name"),
        "price": row.get("current_price"),
        "market_cap": row.get("market_cap"),
        "rank": row.get("market_cap_rank"),
        "volume": row.get("total_volume"),
        "change_1h": row.get("price_change_percentage_1h_in_currency"),
        "change_24h": row.get("price_change_percentage_24h_in_currency")
        or row.get("price_change_percentage_24h"),
        "change_7d": row.get("price_change_percentage_7d_in_currency"),
        "change_30d": row.get("price_change_percentage_30d_in_currency"),
        "ath_change_pct": row.get("ath_change_percentage"),
        "last_updated": row.get("last_updated"),
        "image": row.get("image"),
    }


def _meme_like(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key, "")).lower()
        for key in ("id", "symbol", "name")
    )
    return any(word in haystack for word in MEME_WORDS)


def fetch_trending() -> list[dict[str, Any]]:
    data = _get("search/trending")
    coins = data.get("coins", []) if isinstance(data, dict) else []
    out = []
    for item in coins:
        coin = item.get("item", {}) if isinstance(item, dict) else {}
        if not coin:
            continue
        out.append(
            {
                "id": coin.get("id"),
                "symbol": str(coin.get("symbol", "")).upper(),
                "name": coin.get("name"),
                "rank": coin.get("market_cap_rank"),
                "score": item.get("score"),
                "is_meme_like": _meme_like(coin),
            }
        )
    return out


def fetch_meme_markets(limit: int = 12) -> list[dict[str, Any]]:
    params = {
        "vs_currency": "usd",
        "category": "meme-token",
        "order": "volume_desc",
        "per_page": min(max(limit, 1), 50),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d,30d",
        "precision": "full",
    }
    rows = _get("coins/markets", params=params)
    return [_compact_market(row) for row in rows if isinstance(row, dict)]


def fetch_markets_by_ids(ids: list[str], limit: int = 12) -> list[dict[str, Any]]:
    ids = [coin_id for coin_id in ids if coin_id][:limit]
    if not ids:
        return []
    params = {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": min(max(limit, 1), 50),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d,30d",
        "precision": "full",
    }
    rows = _get("coins/markets", params=params)
    return [_compact_market(row) for row in rows if isinstance(row, dict)]


def search_markets(query: str, limit: int = 6) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    search = _get("search", params={"query": query})
    coins = search.get("coins", []) if isinstance(search, dict) else []
    ids = [coin.get("id") for coin in coins[:limit] if coin.get("id")]
    return fetch_markets_by_ids(ids, limit=limit)


def _score_market(market: dict[str, Any], trending_ids: set[str]) -> dict[str, Any]:
    market_cap = _as_float(market.get("market_cap"))
    volume = _as_float(market.get("volume"))
    change_1h = _as_float(market.get("change_1h"))
    change_24h = _as_float(market.get("change_24h"))
    change_7d = _as_float(market.get("change_7d"))
    change_30d = _as_float(market.get("change_30d"))
    ath_change = _as_float(market.get("ath_change_pct"))
    vol_to_cap = volume / market_cap if market_cap > 0 else 0.0

    score = 42.0
    positives: list[str] = []
    warnings: list[str] = []

    if market.get("id") in trending_ids:
        score += 10
        positives.append("appears in the live trending feed")
    if _meme_like(market):
        score += 5
        positives.append("ticker/name matches meme-coin behavior")
    else:
        score -= 5
        warnings.append("not clearly a meme coin by ticker/name")
    if market_cap >= config.MEME_RADAR_MIN_MARKET_CAP_USD:
        score += 9
        positives.append("market cap is large enough to avoid the smallest traps")
    else:
        score -= 14
        warnings.append("market cap is below the safety floor")
    if volume >= config.MEME_RADAR_MIN_VOLUME_USD:
        score += 11
        positives.append("24h volume clears the liquidity floor")
    else:
        score -= 18
        warnings.append("24h volume is too thin for clean exits")

    if 0.03 <= vol_to_cap <= 0.65:
        score += 10
        positives.append("volume/market-cap ratio shows active trading")
    elif vol_to_cap > 0.65:
        score -= 8
        warnings.append("volume looks overheated versus market cap")
    else:
        score -= 10
        warnings.append("volume/market-cap ratio is weak")

    if 2 <= change_24h <= 35:
        score += 9
        positives.append("24h momentum is positive but not fully vertical")
    elif change_24h > 80:
        score -= 20
        warnings.append("24h move is parabolic; late-entry risk is high")
    elif change_24h < -12:
        score -= 12
        warnings.append("24h momentum is breaking down")

    if -8 <= change_1h <= 12:
        score += 5
    elif abs(change_1h) > 20:
        score -= 12
        warnings.append("1h volatility is too violent for a clean entry")

    if 0 <= change_7d <= 160:
        score += 7
        positives.append("7d trend is constructive without extreme mania")
    elif change_7d > 250:
        score -= 16
        warnings.append("7d move is crowded and vulnerable to mean reversion")
    elif change_7d < -25:
        score -= 10
        warnings.append("7d trend is damaged")

    if change_30d > 300:
        score -= 8
        warnings.append("30d run-up is already very extended")
    if ath_change < -85:
        score -= 4
        warnings.append("token remains deeply below prior hype peak")

    score = round(max(0.0, min(100.0, score)), 1)
    if score >= config.MEME_RADAR_MIN_SCORE and len(warnings) <= 2:
        verdict = "BUY CANDIDATE"
    elif score >= config.MEME_RADAR_WATCH_SCORE:
        verdict = "WATCH"
    else:
        verdict = "AVOID"

    return {
        **market,
        "score": score,
        "verdict": verdict,
        "positives": positives[:4],
        "warnings": warnings[:5],
        "vol_to_cap": vol_to_cap,
    }


def apply_profit_guard(verdict: dict[str, Any], guard: dict[str, Any] | None) -> dict[str, Any]:
    if not guard or not guard.get("active"):
        return verdict
    adjusted = dict(verdict)
    warnings = list(adjusted.get("warnings", []))
    warnings.insert(0, f"Profit Guard is active: {guard.get('reason', 'risk lockout')}")
    adjusted["warnings"] = warnings
    if adjusted.get("verdict") == "BUY CANDIDATE":
        adjusted["verdict"] = "WATCH"
        adjusted["guard_note"] = "Entry downgraded while the main loop is losing."
    return adjusted


def analyze(query: str = "", guard: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a complete Meme Radar payload for the dashboard."""
    if not config.MEME_RADAR_ENABLED:
        return {"ok": False, "error": "Meme Radar is disabled."}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        trending = fetch_trending()
        trending_ids = {item["id"] for item in trending if item.get("id")}
        if query.strip():
            candidates = search_markets(query)
        else:
            try:
                candidates = fetch_meme_markets()
            except Exception:  # noqa: BLE001
                candidates = fetch_markets_by_ids(list(trending_ids))
        if not candidates:
            return {"ok": False, "error": "No matching coin market data found.", "timestamp": now}
        scored = [_score_market(candidate, trending_ids) for candidate in candidates]
        scored.sort(key=lambda row: row.get("score", 0), reverse=True)
        selected = apply_profit_guard(scored[0], guard)
        payload = {
            "ok": True,
            "timestamp": now,
            "query": query.strip(),
            "selected": selected,
            "candidates": scored[:8],
            "trending": trending[:10],
            "source": "coingecko",
        }
        store.append_json_list(config.MEME_RADAR_FILE, payload, cap=200)
        return payload
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "timestamp": now,
            "query": query.strip(),
            "error": f"Meme Radar fetch failed: {exc}",
            "source": "coingecko",
        }
