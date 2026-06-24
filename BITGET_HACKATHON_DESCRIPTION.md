# VesperClaw - Bitget AI Base Camp Hackathon Description

VesperClaw is an accountable AI paper-trading agent built for the Bitget AI Base Camp Hackathon S1, Track 1: Trading Agent. It is designed around a simple idea: a trading agent should explain not only the trades it takes, but also the trades it refuses.

Most trading dashboards lead with a PnL curve. VesperClaw leads with an audit trail. Every cycle produces a market snapshot, a reasoned Signal Mandate, a thesis, a counterargument, an AgentVault risk decision, paper execution records, and post-trade learning. The dashboard turns that trail into a glass-box trading terminal where judges can inspect the agent's reasoning, risk discipline, and self-evaluation.

## What Makes It Different

The core product feature is the Conviction Ledger. VesperClaw tracks two classes of decisions side by side:

- Trades the agent took.
- Trades the agent refused.

When AgentVault blocks or downsizes a trade, VesperClaw does not forget it. The system watches what happened afterward and classifies the refusal as a `good_block` if the market later moved toward the avoided stop, or a `bad_block` if the blocked trade would likely have worked. This creates an accountability loop for inaction, which is usually invisible in trading bots.

That means the agent can be judged on risk judgment, not only raw profit. It can show whether caution saved money, whether the firewall was too strict, and how its future behavior should adapt.

## Decision Lifecycle

Each cycle follows an auditable path:

```text
Bitget market data
  -> Market Snapshot
  -> Regime Referee
  -> Qwen Analyst Council
  -> Signal Mandate
  -> AgentVault risk firewall
  -> Paper execution
  -> Trade close or refusal reconciliation
  -> Evolution engine
  -> Self-briefing
```

The Market Snapshot reads Bitget market data through public feeds and computes indicators such as EMA, ADX, RSI, Bollinger Bands, ATR, funding, open interest context, Fear and Greed sentiment, and keyless GDELT news headlines. If GDELT is slow, it can fall back to public crypto RSS feeds. If a CryptoPanic token is available, VesperClaw can use its crypto-specific community-voted headlines first.

The Regime Referee classifies the market as `trend_up`, `trend_down`, `range`, or `uncertain`. That regime determines which strategy should lead and how confident the agent must be before acting.

The Qwen Analyst Council produces the reasoning layer. Specialized agent roles evaluate trend, mean reversion, risk, sentiment, and allocation. The mandate records both the bullish or bearish thesis and the strongest counterargument, so the decision is not a one-sided justification.

AgentVault is the risk firewall. It can approve, downsize, delay, or reject trades based on position sizing, drawdown, daily loss, volatility, cooldowns, open-position limits, risk/reward, and portfolio exposure across correlated assets.

The Paper Engine simulates fills and exits, then writes a CSV trade log and JSON audit records. No real capital is used by default.

The Evolution Engine updates per-regime strategy weights only after trades close. Learning is deliberately conservative: it requires minimum samples, caps weight changes, and keeps a floor so a noisy sample cannot erase an agent role.

## Current Capabilities

VesperClaw currently supports:

- Multi-asset Bitget basket scanning across BTC, ETH, SOL, and configurable symbols.
- Qwen-powered reasoning through the Bitget hackathon OpenAI-compatible endpoint.
- Deterministic fallback heuristics when no LLM key is available.
- Paper perpetuals with leverage-aware notional sizing and funding-cost awareness.
- Portfolio-wide exposure caps for correlated same-direction positions.
- AgentVault risk gating with reasoned approve, downsize, reject, and delay outcomes.
- Vault Saves, which score blocked trades after the fact.
- Conviction Ledger, which compares taken trades against refused trades.
- Close-based per-regime learning.
- Natural-language contract trading, where a user can describe a perpetuals style and the system compiles validated settings for symbols, leverage, confidence, sizing, and exposure without bypassing AgentVault.
- Prediction-market paper mode for Polymarket-style probability trades, with a 90% target gate that trades only when edge and confidence clear strict thresholds and reports observed accuracy from closed outcomes.
- A Streamlit glass-box dashboard for demo and review.
- Keyless news/trend perception through GDELT and public crypto RSS, with optional CryptoPanic support.

## Dashboard Experience

The upgraded dashboard presents VesperClaw as a trading terminal rather than a default Streamlit app. The first screen shows the project identity, current provider, basket size, leverage, latest vault decision, latest action, equity, return, and last mandate timestamp.

Below that, the dashboard surfaces:

- Key performance metrics.
- The Conviction Ledger for taken and refused trades.
- The agent's self-briefing.
- A market scanner showing the latest decision per asset.
- The latest mandate with thesis, counterargument, invalidation, and agent votes.
- AgentVault decision and checks.
- Snapshot data such as price, ADX, RSI, ATR, EMAs, sentiment, funding, and news bias.
- Equity curve.
- Learned strategy weights per regime.
- Evolution log.
- Mandate ledger.
- Trade log download.
- Prediction-market mode results when present.
- A Contract Command console for natural-language perpetuals profiles.

## Safety Posture

VesperClaw is paper-mode first. The default configuration does not require Bitget API keys and does not place real trades. The safety design includes symbol allowlists, position caps, portfolio exposure caps, max drawdown limits, daily loss limits, volatility vetoes, cooldowns, minimum risk/reward checks, and a complete audit trail.

The project is not trying to present an unstoppable profit machine. It is trying to present a responsible AI trading agent whose reasoning and risk decisions can be inspected, challenged, and improved.

## One-Sentence Pitch

VesperClaw is a Qwen-powered Bitget paper-trading agent that proves its judgment by logging every trade it takes, every trade it refuses, and whether its caution was actually right.
