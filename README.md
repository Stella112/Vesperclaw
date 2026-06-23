# 🦅 VesperClaw — the accountable trading agent

**VesperClaw is the first paper-trading agent that is accountable: it shows you the trades it took, the trades it *refused*, and proves — with receipts — whether each refusal was right.**

Built for the **Bitget AI Base Camp Hackathon S1 — Track 1 (Trading Agent)**.
Qwen-powered. Paper-mode only, no real capital.

🔴 **Live demo (running 24/7):** http://38.49.209.149:8501

---

## The wow: the Conviction Ledger

Every trading bot shows a P&L line. **None of them show the trades they refused — and prove the refusal saved money.** VesperClaw does:

- It logs **every refused trade**, then watches the market and scores it: a **`good_block`** if price later hit the stop it avoided, a **`bad_block`** if it would have won.
- The dashboard's **Conviction Ledger** puts *Taken* and *Refused* side by side, with a running headline like *"7/9 refusals were correct — avg 1.8% adverse move avoided."*
- The agent then **files a plain-English self-briefing**: what it traded, what it refused, whether it was right, and one thing it would do better.

That's the AI-native part: not the signal, but an agent that is **answerable for every action and every inaction.**

## How it earns that

Under the hood, every action flows through an auditable lifecycle:

```
perceive → reason (with a counterargument) → risk-gate → execute → record → self-evaluate
```

You can replay each decision: the thesis *and its strongest counterargument*, the firewall's reasoned approve/refuse, the close, and how outcomes reshaped the agent's per-regime strategy weights. The Conviction Ledger and self-briefing sit on top of this trail.

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

## Run modes

```bash
python main.py --mode fast_demo        # replay real 1m candles fast (judge demo)
python main.py --mode live_paper       # 15m live basket scan, 24/7
python main.py --mode prediction       # prediction markets (Polymarket read feed)
python main.py --vibe "aggressive trend follower, BTC+ETH only, 3x leverage"
```

## Instrument & perception breadth (shipped)

Beyond the multi-asset spot agent, VesperClaw now spans:

- **Perpetuals with funding-aware leverage** — leverage applied to notional,
  funding cost accrued per held bar, liquidation backstop, and extreme funding
  fades the crowded side. (`USE_PERPS`, `LEVERAGE`)
- **Portfolio-level risk** — AgentVault caps *aggregate same-direction exposure*
  across the correlated basket and downsizes to fit remaining room — a portfolio
  risk manager, not just a per-trade gate.
- **On-chain macro signal** — DeFiLlama total-TVL 7-day trend as a risk-on/off
  proxy that nudges directional bias (keyless).
- **Prediction markets** — a **Probability Agent** (Qwen) estimates true odds for
  live Polymarket questions and trades the gap vs. the market-implied price;
  paper-only (read feed, no wallet), probability-move + timeout exits, full audit
  trail. Reuses the same mandate → risk-gate → paper → log skeleton.
- **Natural-language vibe trading** — describe a style in English; Qwen compiles it
  into *validated, range-clamped* config overrides (it can tune the agent, not
  bypass its risk limits).

## Roadmap (room to grow)

- Live (real-capital) execution toggle behind an explicit opt-in.
- Deeper on-chain perception (whale flows, ETF/stablecoin flows) once reliable
  free feeds are wired.
- Per-regime evolution extended to the prediction-market and perps strategies.
- Correlation-matrix-based sizing (currently same-direction aggregate cap).

## Safety

Paper-mode locked by default · no real capital · symbol allowlist · per-symbol & portfolio position caps · size/drawdown/daily-loss limits · cooldowns · volatility veto · complete logged audit trail of every action and refusal.

---

*VesperClaw shows not just whether an agent made money, but **why** every action was taken, **why** unsafe actions were refused, and **how** closed trades evolved its future strategy.*
