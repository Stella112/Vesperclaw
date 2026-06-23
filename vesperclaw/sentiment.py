"""Sentiment and news perception.

Sources:
  * Fear and Greed Index: keyless market-wide crowd sentiment.
  * CryptoPanic headlines: optional token, per-coin community-voted news.
  * GDELT global news: keyless fallback headline stream.

Every source is cached and wrapped in graceful fallbacks, so a third-party API
outage never blocks the trading loop.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
from loguru import logger

import config

_FG_CACHE: dict[str, Any] = {"ts": 0, "value": None, "class": None}
_NEWS_CACHE: dict[str, dict[str, Any]] = {}

_FG_TTL = 600
_NEWS_TTL = 300

_ASSET_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "BNB": "BNB",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "MATIC": "Polygon",
    "TON": "Toncoin",
}

_BULLISH_WORDS = {
    "adopt",
    "adopts",
    "adoption",
    "approve",
    "approved",
    "approval",
    "breakout",
    "bull",
    "bullish",
    "buy",
    "gain",
    "gains",
    "growth",
    "high",
    "inflow",
    "launch",
    "partnership",
    "rally",
    "record",
    "recover",
    "rebound",
    "surge",
    "up",
    "upgrade",
}
_BEARISH_WORDS = {
    "attack",
    "ban",
    "bear",
    "bearish",
    "crackdown",
    "crash",
    "decline",
    "delay",
    "drop",
    "exploit",
    "fall",
    "falls",
    "fear",
    "hack",
    "lawsuit",
    "liquidation",
    "outflow",
    "probe",
    "risk",
    "scam",
    "selloff",
    "slump",
    "warning",
}

_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]


def fetch_fear_greed() -> dict[str, Any]:
    """Return {'value': int|None, 'class': str|None}. Keyless and cached."""
    now = time.time()
    if now - _FG_CACHE["ts"] < _FG_TTL and _FG_CACHE["value"] is not None:
        return {"value": _FG_CACHE["value"], "class": _FG_CACHE["class"]}
    try:
        response = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        response.raise_for_status()
        data = response.json()["data"][0]
        _FG_CACHE.update(
            ts=now,
            value=int(data["value"]),
            **{"class": data["value_classification"]},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"fear and greed fetch failed: {exc}")
        return {"value": None, "class": None}
    return {"value": _FG_CACHE["value"], "class": _FG_CACHE["class"]}


def _base_currency(symbol: str) -> str:
    return symbol.split("/")[0].split(":")[0].upper()


def _empty_news(source: str = "none") -> dict[str, Any]:
    return {"count": 0, "bullish": 0, "bearish": 0, "headlines": [], "source": source}


def _score_headline(title: str) -> int:
    words = {word.strip(".,:;!?()[]{}\"'").lower() for word in title.split()}
    return sum(word in _BULLISH_WORDS for word in words) - sum(word in _BEARISH_WORDS for word in words)


def _gdelt_query(cur: str) -> str:
    name = _ASSET_NAMES.get(cur, cur)
    if cur == name:
        return f'"{name}" cryptocurrency'
    return f'("{name}" OR "{cur}") cryptocurrency'


def fetch_gdelt_news(symbol: str) -> dict[str, Any]:
    """Return keyless per-asset headlines from GDELT's DOC API."""
    cur = _base_currency(symbol)
    cache_key = f"gdelt:{cur}"
    cached = _NEWS_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < _NEWS_TTL:
        return cached["data"]

    try:
        response = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": _gdelt_query(cur),
                "mode": "ArtList",
                "format": "json",
                "maxrecords": 10,
                "timespan": "12h",
                "sort": "hybridrel",
            },
            timeout=8,
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])[:10]
        headlines: list[str] = []
        bullish = bearish = 0
        for article in articles:
            title = article.get("title", "").strip()
            if not title:
                continue
            headlines.append(title)
            score = _score_headline(title)
            if score > 0:
                bullish += 1
            elif score < 0:
                bearish += 1
        data = {
            "count": len(headlines),
            "bullish": bullish,
            "bearish": bearish,
            "headlines": headlines[:5],
            "source": "gdelt",
        }
        _NEWS_CACHE[cache_key] = {"ts": time.time(), "data": data}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"gdelt news fetch failed for {cur}: {exc}")
        return _empty_news("gdelt")


def fetch_rss_news(symbol: str) -> dict[str, Any]:
    """Last-resort keyless RSS headline fallback from public crypto news feeds."""
    cur = _base_currency(symbol)
    name = _ASSET_NAMES.get(cur, cur)
    cache_key = f"rss:{cur}"
    cached = _NEWS_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < _NEWS_TTL:
        return cached["data"]

    needles = {cur.lower(), name.lower()}
    if cur in {"BTC", "ETH", "SOL"}:
        needles.add({"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}[cur])

    headlines: list[str] = []
    bullish = bearish = 0
    for feed_url in _RSS_FEEDS:
        try:
            response = requests.get(feed_url, timeout=8)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                title_l = title.lower()
                if not any(needle in title_l for needle in needles):
                    continue
                if title in headlines:
                    continue
                headlines.append(title)
                score = _score_headline(title)
                if score > 0:
                    bullish += 1
                elif score < 0:
                    bearish += 1
                if len(headlines) >= 10:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"rss news fetch failed from {feed_url}: {exc}")
        if len(headlines) >= 10:
            break

    data = {
        "count": len(headlines),
        "bullish": bullish,
        "bearish": bearish,
        "headlines": headlines[:5],
        "source": "rss",
    }
    _NEWS_CACHE[cache_key] = {"ts": time.time(), "data": data}
    return data


def fetch_cryptopanic_news(symbol: str, token: str) -> dict[str, Any]:
    """Return CryptoPanic headlines when a token is configured."""
    cur = _base_currency(symbol)
    cache_key = f"cryptopanic:{cur}"
    cached = _NEWS_CACHE.get(cache_key)
    if cached and time.time() - cached["ts"] < _NEWS_TTL:
        return cached["data"]

    response = requests.get(
        "https://cryptopanic.com/api/v1/posts/",
        params={"auth_token": token, "currencies": cur, "public": "true"},
        timeout=8,
    )
    response.raise_for_status()
    posts = response.json().get("results", [])[:10]
    bullish = sum(
        1
        for post in posts
        if post.get("votes", {}).get("positive", 0) > post.get("votes", {}).get("negative", 0)
    )
    bearish = sum(
        1
        for post in posts
        if post.get("votes", {}).get("negative", 0) > post.get("votes", {}).get("positive", 0)
    )
    data = {
        "count": len(posts),
        "bullish": bullish,
        "bearish": bearish,
        "headlines": [post.get("title", "") for post in posts[:5] if post.get("title")],
        "source": "cryptopanic",
    }
    _NEWS_CACHE[cache_key] = {"ts": time.time(), "data": data}
    return data


def fetch_news(symbol: str) -> dict[str, Any]:
    """Return {'count', 'bullish', 'bearish', 'headlines', 'source'}.

    CryptoPanic is used when CRYPTOPANIC_TOKEN is present. Without a token, or if
    CryptoPanic fails, VesperClaw falls back to keyless GDELT headlines.
    """
    token = config.CRYPTOPANIC_TOKEN
    if token:
        try:
            return fetch_cryptopanic_news(symbol, token)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"cryptopanic news fetch failed for {_base_currency(symbol)}: {exc}")
    news = fetch_gdelt_news(symbol)
    if news["count"]:
        return news
    return fetch_rss_news(symbol)


def get_sentiment(symbol: str) -> dict[str, Any]:
    """Combined sentiment snapshot for a symbol."""
    fear_greed = fetch_fear_greed()
    news = fetch_news(symbol)
    total = news["bullish"] + news["bearish"]
    news_bias = (news["bullish"] - news["bearish"]) / total if total else 0.0
    return {
        "fear_greed": fear_greed["value"],
        "fg_class": fear_greed["class"],
        "news_count": news["count"],
        "news_bias": round(news_bias, 3),
        "news_source": news.get("source", "none"),
        "headlines": news["headlines"],
    }


if __name__ == "__main__":
    import json

    print(json.dumps(get_sentiment("BTC/USDT"), indent=2))
