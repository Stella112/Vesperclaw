"""Optional Bitget Agent Hub adapter.

This module does not place real orders. It gives VesperClaw a truthful integration
surface for Bitget Agent Hub / Skill Hub:

* detects whether the `bitget-hub` CLI is available through npx
* checks whether Bitget API credentials are configured
* maps the five Skill Hub lanes to VesperClaw's native perception stack
* writes a status artifact for the dashboard and LOOP_STATE.md
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

import config
from vesperclaw import store


SKILLS: list[dict[str, str]] = [
    {
        "id": "macro-analyst",
        "capability": "Macro and cross-asset context",
        "vesperclaw_source": "DeFiLlama TVL trend, funding context, BTC basket regime",
    },
    {
        "id": "market-intel",
        "capability": "On-chain and institutional market intelligence",
        "vesperclaw_source": "onchain.py TVL proxy plus optional future Agent Hub skill",
    },
    {
        "id": "news-briefing",
        "capability": "News aggregation and narrative synthesis",
        "vesperclaw_source": "GDELT, public RSS, optional CryptoPanic",
    },
    {
        "id": "sentiment-analyst",
        "capability": "Sentiment and positioning",
        "vesperclaw_source": "Fear & Greed, funding, long/short placeholder, news bias",
    },
    {
        "id": "technical-analysis",
        "capability": "Technical indicators",
        "vesperclaw_source": "ADX, EMA, RSI, Bollinger, ATR, regime referee",
    },
]


def _run_cli(args: list[str], timeout: int = 12) -> tuple[bool, str]:
    if not shutil.which("npx"):
        return False, "npx not found"
    try:
        proc = subprocess.run(
            ["npx", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    text = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, text[:1000]


def cli_status() -> dict[str, Any]:
    ok, text = _run_cli(["bitget-hub", "--version"], timeout=15)
    if ok:
        return {"available": True, "version": text or "bitget-hub", "detail": "official CLI reachable via npx"}

    # Some npx/npm combinations resolve help but fail version; use help as fallback.
    help_ok, help_text = _run_cli(["bitget-hub", "--help"], timeout=20)
    if help_ok:
        first = help_text.splitlines()[0] if help_text else "bitget-hub"
        return {"available": True, "version": first, "detail": "official CLI reachable via npx"}

    return {"available": False, "version": "not detected", "detail": text or help_text or "bitget-hub unavailable"}


def credential_status() -> dict[str, Any]:
    read_ready = bool(config.BITGET_API_KEY and config.BITGET_SECRET_KEY and config.BITGET_PASSPHRASE)
    return {
        "read_ready": read_ready,
        "trade_ready": read_ready and config.REAL_TRADING_ENABLED,
        "real_trading_enabled": config.REAL_TRADING_ENABLED,
        "mode": "real-trading-enabled" if config.REAL_TRADING_ENABLED else "paper-only-safe",
    }


def status() -> dict[str, Any]:
    cli = cli_status() if config.BITGET_AGENT_HUB_ENABLED else {
        "available": False,
        "version": "disabled",
        "detail": "BITGET_AGENT_HUB_ENABLED=false",
    }
    creds = credential_status()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "enabled": config.BITGET_AGENT_HUB_ENABLED,
        "cli": cli,
        "credentials": creds,
        "mcp_server": {
            "package": "bitget-mcp-server",
            "configured": cli["available"] and creds["read_ready"],
            "mode": creds["mode"],
        },
        "tools": {
            "trading_apis": "available-through-agent-hub" if cli["available"] else "not-detected",
            "execution": "disabled-by-default" if not config.REAL_TRADING_ENABLED else "enabled",
        },
        "skills": [
            {
                **skill,
                "status": "hub-detectable" if cli["available"] else "native-backed",
            }
            for skill in SKILLS
        ],
    }


def write_status() -> dict[str, Any]:
    data = status()
    store.write_json(config.AGENT_HUB_STATUS_FILE, data)
    return data


if __name__ == "__main__":
    import json

    print(json.dumps(write_status(), indent=2))
