# 🦅 VesperClaw

**An autonomous, Qwen-powered Bitget paper-trading agent that turns live market data into explainable Signal Mandates, gates every trade through an AgentVault risk firewall, executes in paper mode, and evolves its strategy weights after closed trades — so it learns which strategy fits each market regime.**

Built for the **Bitget AI Base Camp Hackathon S1 — Track 1 (Trading Agent)**.
Paper-mode only. No real capital. Every decision — including every *refusal* — is logged and replayable.

🔴 **Live demo (running 24/7):** http://38.49.209.149:8501

---

## Why VesperClaw

Static trading bots fail because markets are **regime-based**: a strategy that works in a trend dies in chop. VesperClaw's bet is that the valuable, AI-native part isn't the signal — it's the **full autonomous lifecycle made auditable and self-improving**:

```
perceive → reason (with a counterargument) → risk-gate → execute → record → evolve
```

The headline isn't the P&L. It's the **audit trail**: you can replay every decision, see the thesis *and its strongest counterargument*, watch the firewall refuse a trade, and read how closed trades reshaped the agent's strategy weights.

---

## The loop

```
Bitget market data (multi-asset basket: BTC, ETH, SOL, …)
  → Market Snapshot        (ADX, EMA, RSI, Bollinger, ATR + funding/OI
                            + Fear&Greed / news sentiment)
  → Regime Referee         (ADX decides which strategy may lead)
  → Qwen Analyst Council   (Trend / Mean-Reversion / Risk / Sentiment / Allocator
                            + adversarial debate)
  → Signal Mandate         (action, confidence, SL/TP, thesis, counterargument, votes)
  → AgentVault             (APPROVED / DOWNSIZED / REJECTED / DELAYED;
                            per-symbol + portfolio-wide limits)
  → Paper Execution        (simulated fills + CSV trade log)
  → Close (TP / SL / timeout)
  → Evolution Engine       (per-regime weight learning, close-based)
  → next cycle (scans the whole basket)
```

The agent **scans a basket of symbols every cycle**, detects each one's regime
independently, and can hold concurrent positions across assets under a
portfolio-wide risk cap. Perception fuses price, positioning (funding/OI), and
**crowd sentiment** (Fear & Greed, optional CryptoPanic news) — the Sentiment
agent applies contrarian caution (e.g. it won't chase shorts into Extreme Fear).

### Regime referee
| ADX | Regime | Leading strategy |
|---|---|---|
| ≥ 25, +DI ≥ −DI | `trend_up` | Trend Agent (EMA 9/21 crossover) |
| ≥ 25, −DI > +DI | `trend_down` | Trend Agent (EMA 9/21 crossover) |
| ≤ 20 | `range` | Mean-Reversion Agent (RSI + Bollinger) |
| 20–25 | `uncertain` | No lead — higher confidence required or flat |
| ATR% ≥ danger | any | Risk Agent can veto everything |

Risk management on every trade: **stop-loss 1.5× ATR**, **take-profit 2.5× ATR** (R:R ≈ 1.67), position size scaled by confidence and capped by AgentVault.

---

## Key design choices (the upgrades that matter)

- **Explainable mandates with a built-in counterargument** — every proposed trade records the thesis *and* the strongest case against it (adversarial pass).
- **AgentVault risk firewall** — hard limits on size, daily loss, drawdown, volatility, cooldown, R:R, open positions. Returns a reasoned decision, never a silent block.
- **Vault Saves** — when the firewall blocks/shrinks a trade, it later checks whether that block actually avoided a loss (`good_block` vs `bad_block`).
- **Close-based, per-regime learning** — weights update only when a trade *closes*, learned independently per regime, with sample minimums, capped steps, and a weight floor so noise can't whipsaw the system.
- **Deterministic ground truth + LLM judgment** — Python computes the entry signal (verifiable, reproducible); Qwen supplies confidence and the narrative. If the LLM is unavailable, the loop degrades to heuristics and keeps running.
- **Multi-asset market scan** — every cycle the agent evaluates a basket (BTC, ETH, SOL, …), each with its own regime, holding concurrent positions under a portfolio-wide cap.
- **Sentiment-aware perception** — fuses Fear & Greed and (optional) news flow with price/positioning; the Sentiment agent applies contrarian caution at crowd extremes.

---

## Quick start

```bash
# 1. install
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt

# 2. configure
cp .env.example .env            # then add your QWEN_API_KEY

# 3a. run a fast demo (replays 1-minute candles, full loop in minutes)
python main.py --mode fast_demo --reset

# 3b. or run offline with synthetic candles (no network, no keys)
python main.py --mode fast_demo --demo-data --reset

# 3c. or run live paper mode (one cycle every 15 min)
python main.py --mode live_paper

# 4. watch the glass box
streamlit run dashboard/app.py
```

> **No Bitget API keys are required for paper mode** — market data is pulled from Bitget's public endpoints via `ccxt`. Add keys to `.env` only if you later want authenticated data.

> **Qwen** uses the hackathon OpenAI-compatible endpoint (`https://hackathon.bitgetops.com/v1`). Set `LLM_PROVIDER=claude` with an `ANTHROPIC_API_KEY` to use the fallback. With no key set, the agents run on deterministic heuristics.

---

## Configuration

All tunables live in [`config.py`](config.py) and are overridable via `.env`. Highlights:

| Var | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `qwen` | `qwen` or `claude` |
| `SYMBOL` | `BTC/USDT` | traded pair |
| `RUN_MODE` | `fast_demo` | `fast_demo` or `live_paper` |
| `INITIAL_BALANCE` | `10000` | starting paper equity |
| `RISK_PER_TRADE` | `0.01` | base risk fraction |
| `MAX_POSITION_SIZE_PCT` | `0.10` | vault size cap |
| `MAX_DAILY_LOSS_PCT` | `0.05` | halt new trades after −5% day |
| `MAX_DRAWDOWN_PCT` | `0.20` | lockdown after −20% from peak |

---

## What gets written (the audit trail)

| File | Contents |
|---|---|
| `data/trade_log.csv` | **Required submission artifact** — every fill: timestamp, pair, direction, price, qty, balance change, PnL, mandate id |
| `data/mandates.json` | full Signal Mandates + vault decisions per cycle |
| `data/orders.json` | closed-trade records |
| `data/weights.json` | learned per-regime weights + stats |
| `data/evolution.json` | human-readable weight-change log |
| `data/vault_saves.json` | blocked/downsized trades + good/bad verdicts |
| `data/portfolio.json` | live portfolio state |

---

## Project layout

```
config.py                 # all configuration
main.py                   # orchestrator loop (live_paper + fast_demo)
vesperclaw/
  llm_client.py           # Qwen/Claude provider-agnostic client
  snapshot.py             # market snapshot + ADX regime referee
  sentiment.py            # Fear & Greed + CryptoPanic news perception
  agents.py               # analyst council (incl. sentiment) + adversarial debate
  mandate.py              # Signal Mandate assembler
  vault.py                # AgentVault risk firewall + Vault Saves
  paper_engine.py         # multi-asset paper execution + CSV trade log
  evolution.py            # close-based per-regime learning
  store.py                # JSON/CSV persistence
dashboard/app.py          # Streamlit glass-box dashboard
deploy/                   # systemd services + deploy script (VPS)
```

---

## Deployment

Runs 24/7 on a Linux VPS via two `systemd` services (loop + dashboard). See [`DEPLOY.md`](DEPLOY.md).

---

## Roadmap (room to grow)

VesperClaw's skeleton — Signal Mandate, AgentVault, paper execution + audit log,
and the evolution engine — is **instrument-agnostic**, so new markets plug into the
same explainable, risk-gated loop. Planned next:

- **Perpetuals with funding-aware leverage** — the snapshot already pulls funding
  rate and open interest; the next step is leverage, funding cost in PnL, and
  funding-extreme avoidance.
- **Portfolio-level risk** — correlation-aware sizing and a portfolio drawdown cap,
  elevating AgentVault from per-trade to portfolio risk manager.
- **On-chain signals** — whale flows / DeFi TVL / ETF flows as additional perception
  inputs (pending reliable free data sources).
- **Prediction markets** — a Probability Agent that reads a market's question + news
  to estimate true odds and trade the gap vs. the market-implied price (e.g.
  Polymarket read feed; paper-only, so no wallet needed). The mandate → vault →
  paper → evolution skeleton transfers directly; only the perception/strategy layer
  is new. Probability-move exits keep the close-based learning loop fast.

These are deliberately scoped as future work — the shipped MVP is the multi-asset,
sentiment-aware spot agent above.

## Safety

Paper-mode locked by default · no real capital · symbol allowlist · per-symbol & portfolio position caps · size/drawdown/daily-loss limits · cooldowns · volatility veto · complete logged audit trail of every action and refusal.

---

*VesperClaw shows not just whether an agent made money, but **why** every action was taken, **why** unsafe actions were refused, and **how** closed trades evolved its future strategy.*
