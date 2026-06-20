"""Sentiment & News perception.

Two free-ish sources feed the agent's market awareness:
  * Fear & Greed Index   — alternative.me, KEYLESS, market-wide crowd sentiment.
  * CryptoPanic headlines — optional (needs a free token), per-coin news with
    bullish/bearish community votes.

Both are cached so the loop stays light (and so replay/backtests don't hammer the
APIs). If a source is unavailable the fields are simply None/empty and the agent
degrades gracefully.
"""
from __future__ import annotations

import time
from typing import Any

import requests
from loguru import logger

import config

_FG_CACHE: dict[str, Any] = {"ts": 0, "value": None, "class": None}
_NEWS_CACHE: dict[str, dict[str, Any]] = {}

_FG_TTL = 600       # 10 min — the index updates daily anyway
_NEWS_TTL = 300     # 5 min


def fetch_fear_greed() -> dict[str, Any]:
    """Return {'value': int|None, 'class': str|None}. Keyless. Cached."""
    now = time.time()
    if now - _FG_CACHE["ts"] < _FG_TTL and _FG_CACHE["value"] is not None:
        return {"value": _FG_CACHE["value"], "class": _FG_CACHE["class"]}
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        _FG_CACHE.update(ts=now, value=int(d["value"]), **{"class": d["value_classification"]})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"fear&greed fetch failed: {e}")
        return {"value": None, "class": None}
    return {"value": _FG_CACHE["value"], "class": _FG_CACHE["class"]}


def _base_currency(symbol: str) -> str:
    return symbol.split("/")[0].split(":")[0].upper()


def fetch_news(symbol: str) -> dict[str, Any]:
    """Return {'count': int, 'bullish': int, 'bearish': int, 'headlines': [..]}.

    Requires CRYPTOPANIC_TOKEN; without it returns an empty record.
    """
    token = config.CRYPTOPANIC_TOKEN
    empty = {"count": 0, "bullish": 0, "bearish": 0, "headlines": []}
    if not token:
        return empty

    cur = _base_currency(symbol)
    cached = _NEWS_CACHE.get(cur)
    if cached and time.time() - cached["ts"] < _NEWS_TTL:
        return cached["data"]

    try:
        r = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": token, "currencies": cur, "public": "true"},
            timeout=8,
        )
        posts = r.json().get("results", [])[:10]
        bullish = sum(1 for p in posts if p.get("votes", {}).get("positive", 0) > p.get("votes", {}).get("negative", 0))
        bearish = sum(1 for p in posts if p.get("votes", {}).get("negative", 0) > p.get("votes", {}).get("positive", 0))
        data = {
            "count": len(posts),
            "bullish": bullish,
            "bearish": bearish,
            "headlines": [p.get("title", "") for p in posts[:5]],
        }
        _NEWS_CACHE[cur] = {"ts": time.time(), "data": data}
        return data
    except Exception as e:  # noqa: BLE001
        logger.debug(f"news fetch failed for {cur}: {e}")
        return empty


def get_sentiment(symbol: str) -> dict[str, Any]:
    """Combined sentiment snapshot for a symbol."""
    fg = fetch_fear_greed()
    news = fetch_news(symbol)
    # news bias in [-1, 1]
    total = news["bullish"] + news["bearish"]
    news_bias = (news["bullish"] - news["bearish"]) / total if total else 0.0
    return {
        "fear_greed": fg["value"],
        "fg_class": fg["class"],
        "news_count": news["count"],
        "news_bias": round(news_bias, 3),
        "headlines": news["headlines"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_sentiment("BTC/USDT"), indent=2))
