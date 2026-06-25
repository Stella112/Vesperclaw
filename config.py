"""VesperClaw central configuration.

All tunables live here so the loop, agents, vault, and dashboard read one source
of truth. Values come from environment variables (.env) with safe defaults.
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ── Reasoning provider ────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "qwen").lower()  # "qwen" | "claude"

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://hackathon.bitgetops.com/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.6-plus")
QWEN_FAST_MODEL = os.getenv("QWEN_FAST_MODEL", "qwen3.6-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


# ── Bitget (optional — paper mode uses public market data) ────────────
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
PLAYBOOK_API_KEY = os.getenv("PLAYBOOK_API_KEY", "")
BITGET_AGENT_HUB_ENABLED = os.getenv("BITGET_AGENT_HUB_ENABLED", "true").lower() == "true"
REAL_TRADING_ENABLED = os.getenv("REAL_TRADING_ENABLED", "false").lower() == "true"

# Sentiment & news (Fear & Greed is keyless; CryptoPanic token is optional)
CRYPTOPANIC_TOKEN = os.getenv("CRYPTOPANIC_TOKEN", "")
USE_SENTIMENT = os.getenv("USE_SENTIMENT", "true").lower() == "true"

# Meme Radar: keyless CoinGecko scanner for judge/user-entered meme coins.
# It is a recommendation gate only; it never places orders.
MEME_RADAR_ENABLED = os.getenv("MEME_RADAR_ENABLED", "true").lower() == "true"
MEME_RADAR_API_BASE = os.getenv("MEME_RADAR_API_BASE", "https://api.coingecko.com/api/v3")
MEME_RADAR_API_KEY = os.getenv("MEME_RADAR_API_KEY", "")
MEME_RADAR_MIN_SCORE = float(os.getenv("MEME_RADAR_MIN_SCORE", "75"))
MEME_RADAR_WATCH_SCORE = float(os.getenv("MEME_RADAR_WATCH_SCORE", "55"))
MEME_RADAR_MIN_VOLUME_USD = float(os.getenv("MEME_RADAR_MIN_VOLUME_USD", "1000000"))
MEME_RADAR_MIN_MARKET_CAP_USD = float(os.getenv("MEME_RADAR_MIN_MARKET_CAP_USD", "10000000"))
MEME_RADAR_FILE = os.getenv("MEME_RADAR_FILE", f"{os.getenv('DATA_DIR', 'data')}/meme_radar.json")


# ── Trading config ────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
# Multi-asset basket the agent scans each cycle. Defaults to a 3-coin basket;
# override with SYMBOL_ALLOWLIST="BTC/USDT,ETH/USDT" etc. Always includes SYMBOL.
_allow_default = "BTC/USDT,ETH/USDT,SOL/USDT"
SYMBOL_ALLOWLIST = [s.strip() for s in os.getenv("SYMBOL_ALLOWLIST", _allow_default).split(",") if s.strip()]
if SYMBOL not in SYMBOL_ALLOWLIST:
    SYMBOL_ALLOWLIST.insert(0, SYMBOL)
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))
TAKER_FEE = float(os.getenv("TAKER_FEE", "0.0006"))  # 0.06% Bitget taker

# Perpetuals: leverage applies to notional (size_pct is the margin fraction).
# LEVERAGE=1 keeps spot-like behaviour. Funding cost accrues per held bar.
USE_PERPS = os.getenv("USE_PERPS", "true").lower() == "true"
LEVERAGE = float(os.getenv("LEVERAGE", "2")) if USE_PERPS else 1.0
FUNDING_INTERVAL_BARS = float(os.getenv("FUNDING_INTERVAL_BARS", "32"))  # ~8h in 15m bars
EXTREME_FUNDING = float(os.getenv("EXTREME_FUNDING", "0.0005"))  # per-interval, ~crowded

# Run mode
RUN_MODE = os.getenv("RUN_MODE", "fast_demo").lower()  # "live_paper" | "fast_demo"
DEMO_DATA = os.getenv("DEMO_DATA", "false").lower() == "true"

# Cycle cadence + replay sizing per mode
if RUN_MODE == "fast_demo":
    LOOP_TIMEFRAME = "1m"
    LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", "5"))
else:  # live_paper
    LOOP_TIMEFRAME = TIMEFRAME
    LOOP_INTERVAL_SECONDS = int(os.getenv("LOOP_INTERVAL_SECONDS", str(15 * 60)))


# ── Regime referee (ADX thresholds) ───────────────────────────────────
ADX_TREND_MIN = 25.0      # >= -> trend regime
ADX_RANGE_MAX = 20.0      # <= -> range regime
# between the two -> uncertain (needs higher confidence or no trade)
UNCERTAIN_CONFIDENCE_BONUS = 0.10  # added to MIN_CONFIDENCE when uncertain

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14

# Risk-management multiples (ATR-based)
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.5
TIMEOUT_BARS = int(os.getenv("TIMEOUT_BARS", "16"))  # force-close stale trades


# ── AgentVault limits ─────────────────────────────────────────────────
MAX_POSITION_SIZE_PCT = 0.10   # max 10% of equity notional per trade
MIN_CONFIDENCE = 0.55
MAX_DAILY_LOSS_PCT = 0.05      # halt new trades after -5% day
MAX_DRAWDOWN_PCT = 0.20        # lockdown after -20% from peak
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))   # portfolio-wide cap
MAX_POSITIONS_PER_SYMBOL = 1   # at most one open position per symbol
# Portfolio-level: the basket (BTC/ETH/SOL) is highly correlated, so same-direction
# positions add up as one risk. Cap aggregate same-direction notional exposure.
MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.18"))
DANGER_VOLATILITY_PCT = 5.0    # ATR as % of price -> Risk Agent veto
COOLDOWN_BARS = 4
MIN_RR = 1.5                   # minimum risk/reward ratio

# Profit Guard: tighten the agent after losses instead of letting it bleed in chop.
PROFIT_GUARD_ENABLED = os.getenv("PROFIT_GUARD_ENABLED", "true").lower() == "true"
PROFIT_GUARD_LOSS_STREAK = int(os.getenv("PROFIT_GUARD_LOSS_STREAK", "2"))
PROFIT_GUARD_COOLDOWN_BARS = int(os.getenv("PROFIT_GUARD_COOLDOWN_BARS", "24"))
PROFIT_GUARD_MIN_CONFIDENCE_BONUS = float(os.getenv("PROFIT_GUARD_MIN_CONFIDENCE_BONUS", "0.15"))
PROFIT_GUARD_MAX_SIZE_PCT = float(os.getenv("PROFIT_GUARD_MAX_SIZE_PCT", "0.035"))
PROFIT_GUARD_DRAWDOWN_PCT = float(os.getenv("PROFIT_GUARD_DRAWDOWN_PCT", "0.01"))
PROFIT_GUARD_DAILY_LOSS_PCT = float(os.getenv("PROFIT_GUARD_DAILY_LOSS_PCT", "0.005"))
PROFIT_GUARD_BLOCK_REGIMES = [
    r.strip() for r in os.getenv("PROFIT_GUARD_BLOCK_REGIMES", "uncertain").split(",") if r.strip()
]


# ── Evolution engine ──────────────────────────────────────────────────
EVO_MIN_SAMPLES = 5            # closed trades per regime before adjusting
EVO_STEP_CAP = 0.05           # max weight change per update
EVO_WEIGHT_FLOOR = 0.10       # no agent ever drops below this

# How often (cycles) to regenerate the agent's self-briefing
BRIEFING_EVERY_CYCLES = int(os.getenv("BRIEFING_EVERY_CYCLES", "20"))

DEFAULT_WEIGHTS = {
    "trend_agent": 0.35,
    "mean_reversion_agent": 0.30,
    "risk_agent": 0.20,
    "allocator_agent": 0.15,
}
REGIMES = ["trend_up", "trend_down", "range", "uncertain"]


# ── Storage ───────────────────────────────────────────────────────────
# ── Prediction markets (#6) ───────────────────────────────────────────
PRED_TARGET_ACCURACY = float(os.getenv("PRED_TARGET_ACCURACY", "0.90"))
PRED_MIN_CONFIDENCE = float(os.getenv("PRED_MIN_CONFIDENCE", "0.70"))
PRED_EDGE_THRESHOLD = float(os.getenv("PRED_EDGE_THRESHOLD", "0.10"))  # min |est-market| to trade
PRED_MAX_POSITIONS = int(os.getenv("PRED_MAX_POSITIONS", "3"))
PRED_SIZE_PCT = float(os.getenv("PRED_SIZE_PCT", "0.05"))
PRED_STOP_BAND = float(os.getenv("PRED_STOP_BAND", "0.06"))   # YES-price stop distance
PRED_TIMEOUT_BARS = int(os.getenv("PRED_TIMEOUT_BARS", "10"))
PRED_INITIAL_BALANCE = float(os.getenv("PRED_INITIAL_BALANCE", "10000"))
PRED_MARKETS = int(os.getenv("PRED_MARKETS", "8"))            # markets scanned per cycle
PRED_INCLUDE_FOOTBALL = os.getenv("PRED_INCLUDE_FOOTBALL", "true").lower() == "true"
PRED_FOOTBALL_MARKETS = int(os.getenv("PRED_FOOTBALL_MARKETS", "4"))
PRED_INCLUDE_WORLD_CUP = os.getenv("PRED_INCLUDE_WORLD_CUP", "true").lower() == "true"
PRED_WORLD_CUP_MARKETS = int(os.getenv("PRED_WORLD_CUP_MARKETS", "6"))


# ── Storage ───────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "data")
PORTFOLIO_FILE = f"{DATA_DIR}/portfolio.json"
MANDATES_FILE = f"{DATA_DIR}/mandates.json"
ORDERS_FILE = f"{DATA_DIR}/orders.json"
EVOLUTION_FILE = f"{DATA_DIR}/evolution.json"
WEIGHTS_FILE = f"{DATA_DIR}/weights.json"
VAULT_SAVES_FILE = f"{DATA_DIR}/vault_saves.json"
TRADE_LOG_CSV = f"{DATA_DIR}/trade_log.csv"
PROFILE_FILE = f"{DATA_DIR}/profile.json"   # natural-language "vibe" overrides
BRIEFING_FILE = f"{DATA_DIR}/briefing.json"  # agent's accountability self-briefing
LOOP_STATE_FILE = f"{DATA_DIR}/LOOP_STATE.md"  # human-readable loop memory
AGENT_HUB_STATUS_FILE = f"{DATA_DIR}/agent_hub_status.json"
# prediction-market state (kept separate from the crypto loop)
PRED_PORTFOLIO_FILE = f"{DATA_DIR}/pred_portfolio.json"
PRED_MANDATES_FILE = f"{DATA_DIR}/pred_mandates.json"
PRED_ORDERS_FILE = f"{DATA_DIR}/pred_orders.json"
PRED_TRADE_LOG_CSV = f"{DATA_DIR}/pred_trade_log.csv"
