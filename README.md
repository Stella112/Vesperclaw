# VesperClaw

**An accountable Bitget paper-trading agent for AI Base Camp Hackathon S1, Track 1.**

VesperClaw is a closed-loop trading agent that scans crypto markets, writes a trade thesis, checks every decision through a deterministic risk firewall, executes only approved paper trades, and records what happened next.

The project is not presented as a magic profit machine. Its edge is **accountable autonomy**: every trade, refusal, risk check, and post-trade lesson is inspectable.

## Live Links

| Material | Link |
|---|---|
| MuleRun demo page | https://zgzcjrl6.mule.page/#firewall |
| Live dashboard | http://38.49.209.149:8501/ |
| Paper trading log | [`samples/trade_log.csv`](samples/trade_log.csv) |
| Prediction-market paper log | [`samples/pred_trade_log.csv`](samples/pred_trade_log.csv) |
| Sample mandates and outputs | [`samples/`](samples/) |

## Why VesperClaw Exists

Most trading agents show the final trade and hide the process. VesperClaw shows the process first.

Every cycle produces:

- a market snapshot,
- a proposed mandate,
- a thesis and counterargument,
- an Alpha Gate quality check,
- an AgentVault risk decision,
- a paper fill or refusal,
- a post-trade record.

This makes the agent judgeable. A reviewer can ask: did it trade for a reason, did it refuse for a reason, and did the risk system protect capital when the signal was weak?

## Strategy Logic

VesperClaw currently scans a crypto basket:

- `BTC/USDT`
- `ETH/USDT`
- `SOL/USDT`

The agent uses technical and market-state signals including:

- EMA trend direction,
- RSI,
- ATR,
- Bollinger Bands,
- ADX trend strength,
- volume behavior,
- higher-timeframe confirmation,
- funding and market context,
- Fear & Greed / news-sentiment lanes where available.

The decision engine turns those signals into a structured mandate:

```text
symbol -> side -> confidence -> thesis -> counterargument -> stop -> target -> size
```

Then the mandate must survive two safety layers before any paper trade is opened.

## Safety Architecture

### 1. Alpha Gate

Alpha Gate is the quality filter before risk execution. It blocks weak entries unless the setup has enough trend and confirmation.

It checks:

- trend-only mode,
- 1-hour higher-timeframe confirmation,
- minimum ADX,
- no falling-volume entries,
- no overextended RSI chases,
- direction not fighting recent movement.

SOL remains in the basket, but it must pass the same gate as BTC and ETH.

### 2. AgentVault

AgentVault is the deterministic risk firewall. It can approve, downsize, delay, or reject a mandate.

It checks:

- symbol allowlist,
- confidence floor,
- leverage,
- requested position size,
- portfolio exposure,
- open-position limits,
- drawdown,
- daily loss,
- cooldown,
- volatility regime,
- minimum risk/reward.

Rejected trades are logged instead of silently ignored.

### 3. Profit Guard

Profit Guard protects capital after losses. When active, it raises the confidence bar, reduces size, blocks choppy regimes, and can lock new entries when drawdown or daily loss limits are hit.

## Agent Loop

```text
Perceive -> Propose -> Verify -> Execute -> Monitor -> Learn -> Repeat
```

| Stage | What happens |
|---|---|
| Perceive | Pull market data and build indicator snapshots |
| Propose | Generate a mandate with thesis and counterargument |
| Verify | Alpha Gate and AgentVault check the mandate |
| Execute | Approved trades enter the paper book |
| Monitor | Positions are marked to market and closed by stop, target, or timeout |
| Learn | Closed outcomes and refused trades update the audit trail |

## Evolution Memory

VesperClaw is designed to improve from outcomes, not just generate one-off signals.

When a paper trade closes, the Evolution Engine records what happened and updates the agent's memory by regime. A trend trade in a strong market should not teach the same lesson as a range trade in a choppy market, so VesperClaw keeps learning tied to market context.

It tracks:

- which agent role led the decision,
- the regime at entry,
- whether the trade won or lost,
- whether the thesis was validated,
- whether AgentVault saved the agent from a bad trade,
- what should be weighted more or less in future cycles.

Learning is intentionally conservative. VesperClaw updates only after closed outcomes, uses capped weight changes, keeps minimum sample requirements, and avoids letting one noisy trade rewrite the whole strategy.

The dashboard exposes this through evolution logs, strategy weights, refused-trade scoring, and the Conviction Ledger.

## Extra Agent Surfaces

### Meme Radar

The dashboard includes a meme-coin search tool. A user can search a ticker or name, and VesperClaw returns:

- `BUY CANDIDATE`
- `WATCH`
- `AVOID`

The score uses liquidity, market cap, volume, momentum, volatility, trending status, and the current Profit Guard state. It is analysis-only and never auto-executes.

### Prediction Markets

VesperClaw also has a paper prediction-market lane. It scans football and World Cup markets, estimates probability, compares it with market-implied odds, and only paper-trades when the edge and confidence clear the gate.

This is separate from crypto execution and is used to show the same agent discipline in probability markets.

## Bitget Agent Hub

VesperClaw includes a Bitget Agent Hub readiness adapter.

It surfaces:

- official Agent Hub / CLI readiness,
- API credential readiness,
- paper-only safety mode,
- Skill Hub lanes for macro, market intel, news, sentiment, and technical analysis.

Real trading is disabled by default:

```text
REAL_TRADING_ENABLED=false
```

The deployed demo uses read-only readiness and live-paper execution.

## Proof Artifacts

The hackathon-required paper log is available here:

[`samples/trade_log.csv`](samples/trade_log.csv)

It includes:

- timestamp,
- trading pair,
- direction,
- event,
- price,
- quantity,
- notional,
- fee,
- balance before,
- balance after,
- PnL,
- regime,
- vault decision.

Prediction-market paper logs are available here:

[`samples/pred_trade_log.csv`](samples/pred_trade_log.csv)

Additional sample outputs are in [`samples/`](samples/).

## Dashboard

The live dashboard is designed as a judge-facing trading terminal.

It shows:

- equity,
- paper PnL,
- unrealized PnL,
- closed trades,
- win rate,
- current cycle,
- Alpha Gate status,
- latest mandate,
- Profit Guard state,
- World Cup prediction board,
- Meme Radar,
- Bitget Agent Hub readiness,
- Conviction Ledger,
- evolution memory,
- strategy weights,
- AgentVault checks,
- paper trade logs.

Live dashboard:

http://38.49.209.149:8501/

## Quick Start

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

Run a fast local demo:

```bash
python main.py --mode fast_demo --demo-data --reset
streamlit run dashboard/app.py
```

Run live-paper mode:

```bash
python main.py --mode live_paper
streamlit run dashboard/app.py
```

Paper mode can run without Bitget API keys using public market data. API keys are only needed for authenticated data or future real execution. Real trading remains off unless explicitly enabled.

## Configuration

Most settings are in [`config.py`](config.py) and can be overridden with `.env`.

| Variable | Purpose |
|---|---|
| `RUN_MODE` | `fast_demo` or `live_paper` |
| `SYMBOL_ALLOWLIST` | Crypto basket, defaults to BTC, ETH, SOL |
| `LEVERAGE` | Paper perpetual leverage |
| `MIN_CONFIDENCE` | Minimum confidence before risk checks |
| `MAX_OPEN_POSITIONS` | Portfolio position limit |
| `TRADE_ONLY_TREND` | Requires trend setups |
| `REQUIRE_HTF_CONFIRMATION` | Requires higher-timeframe confirmation |
| `MIN_TREND_ADX` | Minimum trend strength |
| `REAL_TRADING_ENABLED` | Must be true before real execution can be enabled |

## Project Structure

```text
config.py                 central configuration
main.py                   autonomous loop
dashboard/app.py          Streamlit dashboard
vesperclaw/agents.py      analyst council and debate
vesperclaw/mandate.py     structured trade mandate
vesperclaw/vault.py       AgentVault risk firewall
vesperclaw/paper_engine.py paper execution and trade log
vesperclaw/evolution.py   close-based self-improvement memory
vesperclaw/prediction.py  prediction-market paper agent
vesperclaw/meme_radar.py  meme coin analysis
vesperclaw/agent_hub.py   Bitget Agent Hub readiness
samples/                  public sample logs and outputs
deploy/                   VPS deployment files
```

## Current Status

Completed:

- live public dashboard,
- public MuleRun demo page,
- public GitHub repo,
- paper trading log,
- AgentVault,
- Alpha Gate,
- Profit Guard,
- Meme Radar,
- World Cup prediction board,
- Bitget Agent Hub readiness,
- evolution memory from closed outcomes,
- sample output artifacts.

Still improving:

- profitability and longer forward testing,
- deeper live sentiment/news feeds,
- reproducible backtest notebook,
- fuller Bitget Skill Hub integration,
- optional real execution after paper performance is stable.

## One-Line Pitch

**VesperClaw is an accountable AI trading agent that can trade, refuse, explain, and improve, with public logs to prove its decisions.**
