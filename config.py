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


# ── Trading config ────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
SYMBOL_ALLOWLIST = [s.strip() for s in os.getenv("SYMBOL_ALLOWLIST", SYMBOL).split(",")]
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))
TAKER_FEE = float(os.getenv("TAKER_FEE", "0.0006"))  # 0.06% Bitget taker

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
MAX_OPEN_POSITIONS = 1
DANGER_VOLATILITY_PCT = 5.0    # ATR as % of price -> Risk Agent veto
COOLDOWN_BARS = 4
MIN_RR = 1.5                   # minimum risk/reward ratio


# ── Evolution engine ──────────────────────────────────────────────────
EVO_MIN_SAMPLES = 5            # closed trades per regime before adjusting
EVO_STEP_CAP = 0.05           # max weight change per update
EVO_WEIGHT_FLOOR = 0.10       # no agent ever drops below this

DEFAULT_WEIGHTS = {
    "trend_agent": 0.35,
    "mean_reversion_agent": 0.30,
    "risk_agent": 0.20,
    "allocator_agent": 0.15,
}
REGIMES = ["trend_up", "trend_down", "range", "uncertain"]


# ── Storage ───────────────────────────────────────────────────────────
DATA_DIR = os.getenv("DATA_DIR", "data")
PORTFOLIO_FILE = f"{DATA_DIR}/portfolio.json"
MANDATES_FILE = f"{DATA_DIR}/mandates.json"
ORDERS_FILE = f"{DATA_DIR}/orders.json"
EVOLUTION_FILE = f"{DATA_DIR}/evolution.json"
WEIGHTS_FILE = f"{DATA_DIR}/weights.json"
VAULT_SAVES_FILE = f"{DATA_DIR}/vault_saves.json"
TRADE_LOG_CSV = f"{DATA_DIR}/trade_log.csv"
