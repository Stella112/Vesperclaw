"""Natural-language "vibe trading" (#7).

The user describes a trading style in plain English; Qwen compiles it into a
*validated* set of config overrides (a profile). Only whitelisted keys are
accepted and every value is clamped to a safe range — the LLM can tune the agent,
not jailbreak its risk limits. The profile persists to data/profile.json and the
loop applies it at startup.

    python main.py --vibe "aggressive trend follower, BTC & ETH only, 3x leverage,
                           take fewer but higher-conviction trades"
"""
from __future__ import annotations

from typing import Any

from loguru import logger

import config
from vesperclaw import store
from vesperclaw.llm_client import get_client

# whitelist: key -> (min, max) for numeric clamping
_NUMERIC = {
    "MIN_CONFIDENCE": (0.40, 0.90),
    "MAX_POSITION_SIZE_PCT": (0.02, 0.25),
    "RISK_PER_TRADE": (0.005, 0.05),
    "LEVERAGE": (1.0, 5.0),
    "MAX_OPEN_POSITIONS": (1, 6),
    "MAX_PORTFOLIO_EXPOSURE_PCT": (0.05, 0.50),
    "SL_ATR_MULT": (0.5, 4.0),
    "TP_ATR_MULT": (0.5, 6.0),
    "ADX_TREND_MIN": (15.0, 40.0),
    "ADX_RANGE_MAX": (10.0, 25.0),
}
_BOOL = {"USE_SENTIMENT", "USE_PERPS"}
_SYMBOL_UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "DOGE/USDT"]

VIBE_SYS = (
    "You translate a trader's plain-English style into JSON config overrides for an "
    "autonomous crypto agent. Only use these keys when relevant: "
    + ", ".join(list(_NUMERIC) + sorted(_BOOL) + ["SYMBOL_ALLOWLIST"]) + ". "
    "SYMBOL_ALLOWLIST is a subset of " + ", ".join(_SYMBOL_UNIVERSE) + ". "
    "Higher conviction => higher MIN_CONFIDENCE. Aggressive => higher size/leverage. "
    "Return ONLY a JSON object of the keys you want to change."
)


def compile_profile(nl_text: str) -> dict[str, Any]:
    """Compile NL -> validated overrides dict. Unknown keys/out-of-range are dropped/clamped."""
    client = get_client()
    raw = client.chat_json(
        VIBE_SYS,
        f"Trader style: {nl_text}\nReturn the JSON overrides.",
        fallback={},
        fast=False,
    )
    profile: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _NUMERIC:
            try:
                lo, hi = _NUMERIC[k]
                val = max(lo, min(hi, float(v)))
                profile[k] = int(val) if isinstance(_NUMERIC[k][0], int) else round(val, 4)
            except (TypeError, ValueError):
                continue
        elif k in _BOOL:
            profile[k] = bool(v)
        elif k == "SYMBOL_ALLOWLIST":
            if isinstance(v, list):
                syms = [s for s in v if s in _SYMBOL_UNIVERSE]
                if syms:
                    profile[k] = syms
    return profile


def save_profile(profile: dict[str, Any], source: str = "") -> None:
    store.write_json(config.PROFILE_FILE, {"source": source, "overrides": profile})


def load_profile() -> dict[str, Any]:
    data = store.read_json(config.PROFILE_FILE, {})
    return data.get("overrides", {}) if isinstance(data, dict) else {}


def apply_profile(profile: dict[str, Any]) -> None:
    """Mutate the live config module with the validated overrides."""
    for k, v in profile.items():
        setattr(config, k, v)
    if profile:
        logger.info(f"Vibe profile applied: {profile}")


def set_vibe(nl_text: str) -> dict[str, Any]:
    """Compile, persist, and apply a NL style. Returns the profile."""
    profile = compile_profile(nl_text)
    save_profile(profile, source=nl_text)
    apply_profile(profile)
    return profile


if __name__ == "__main__":
    import sys
    import json
    text = " ".join(sys.argv[1:]) or "balanced, moderate risk, BTC and ETH only"
    print(json.dumps(set_vibe(text), indent=2))
