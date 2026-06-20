# Sample run artifacts (verifiable usage record)

These files are real outputs from VesperClaw running on **live Bitget BTC/USDT
market data** in paper mode. They satisfy the hackathon's "verifiable usage
record" requirement and let judges inspect the full decision lifecycle without
running anything.

| File | What it shows |
|---|---|
| [`trade_log.csv`](trade_log.csv) | **Required artifact.** Multi-asset (BTC/ETH/SOL) paper fills: timestamp, pair, direction, price, quantity, fee, balance before/after, PnL, regime, vault decision, linked mandate id. |
| [`sample_qwen_mandate.json`](sample_qwen_mandate.json) | One full **Qwen-powered Signal Mandate** — thesis, the adversarial **counterargument**, per-agent votes with distinct reasoning, SL/TP, R:R, and invalidation condition. |
| [`vault_saves_sample.json`](vault_saves_sample.json) | Trades the AgentVault firewall blocked/downsized, later reconciled as `good_block` / `bad_block`. |
| [`evolution_sample.json`](evolution_sample.json) | Close-based, per-regime weight changes with human-readable reasons. |
| [`pred_trade_log.csv`](pred_trade_log.csv) | **Prediction-market** paper trades on live Polymarket markets (Probability Agent vs market-implied odds). |
| [`pred_mandates_sample.json`](pred_mandates_sample.json) | Prediction-market decisions: question, market price, Qwen's estimated probability, the edge, and the trade/refusal. |

## Notes for reviewers

- The **live demo** (`http://38.49.209.149:8501`) runs the same loop continuously
  with Qwen reasoning enabled, and keeps appending to its own audit trail.
- The trade log here is a short replay window on recent real Bitget 1-minute
  candles. **The point of VesperClaw is the audited decision process** — the
  thesis + counterargument, the firewall's reasoned blocks, and per-regime
  learning — not the P&L of any single short sample.
- Every entry/exit is deterministic and reproducible from the code; Qwen supplies
  the confidence and narrative on top of the deterministic signal.
