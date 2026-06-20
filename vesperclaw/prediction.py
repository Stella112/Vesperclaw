"""Prediction markets mode (#6) — a second instrument class.

Prediction markets price *probability*: a YES share trades at $0.00–$1.00 = the
market's implied odds. The edge isn't charts — it's estimating the true
probability of an event and trading the gap vs. the market price. That's a
natural fit for an LLM agent.

Flow (reuses VesperClaw's explainable, risk-gated, audited skeleton):
    Polymarket read feed  ->  Probability Agent (Qwen: fair odds + thesis)
      ->  edge vs market   ->  risk gate  ->  paper buy YES/NO
      ->  exit on probability move / stop / timeout  ->  audit log

No wallet or capital is needed — we only READ Polymarket prices and simulate the
fills. If the feed is unavailable it falls back to synthetic markets so the mode
is always runnable.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import requests
from loguru import logger

import config
from vesperclaw import store
from vesperclaw.llm_client import get_client

GAMMA_URL = "https://gamma-api.polymarket.com/markets"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── market data ──────────────────────────────────────────────────────────

def fetch_markets(limit: int) -> list[dict[str, Any]]:
    """Return liquid binary markets: {id, question, yes_price, volume}."""
    try:
        r = requests.get(
            GAMMA_URL,
            params={"closed": "false", "active": "true", "order": "volume",
                    "ascending": "false", "limit": limit * 3},
            timeout=10,
        )
        out = []
        for m in r.json():
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if not prices:
                continue
            yes = float(prices[0])
            if 0.05 < yes < 0.95:  # skip near-resolved markets
                out.append({
                    "id": str(m.get("id")),
                    "question": m.get("question", "")[:140],
                    "yes_price": round(yes, 3),
                    "volume": float(m.get("volume") or 0),
                })
            if len(out) >= limit:
                break
        if out:
            logger.info(f"Polymarket: {len(out)} live markets fetched.")
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Polymarket fetch failed ({e}); using synthetic markets.")
    return _synthetic_markets(limit)


def _synthetic_markets(limit: int) -> list[dict[str, Any]]:
    import numpy as np
    rng = np.random.default_rng(int(time.time()) % 9999)
    qs = [
        "Will BTC close above $80k this month?",
        "Will the Fed cut rates at the next meeting?",
        "Will ETH flip its prior all-time high this quarter?",
        "Will a spot SOL ETF be approved this year?",
        "Will total crypto market cap exceed $3T this month?",
        "Will US CPI come in below forecast next print?",
        "Will a major exchange list this memecoin this week?",
        "Will gas fees on Ethereum spike above 100 gwei this week?",
    ]
    return [
        {"id": f"SYN-{i}", "question": qs[i % len(qs)],
         "yes_price": round(float(rng.uniform(0.2, 0.8)), 3),
         "volume": float(rng.uniform(1e4, 1e6))}
        for i in range(min(limit, len(qs)))
    ]


# ── probability agent ─────────────────────────────────────────────────────

PROB_SYS = (
    "You are the Probability Agent for a prediction-market trader. Given a market "
    "question and the market's implied probability, estimate the TRUE probability of "
    "YES using base rates and reasoning. Be calibrated and honest; if you have no "
    "edge, return a probability close to the market's. Concise."
)


def estimate_probability(question: str, market_yes: float) -> dict[str, Any]:
    """Return {prob, confidence, thesis, counterargument}. Falls back to no-edge."""
    client = get_client()
    fallback = {
        "prob": market_yes, "confidence": 0.3,
        "thesis": "No independent edge; deferring to market-implied odds.",
        "counterargument": "Market price already aggregates available information.",
    }
    user = (
        f"Market question: {question}\n"
        f"Market-implied P(YES) = {market_yes:.2f}\n"
        f'Respond ONLY with JSON: {{"prob":0.0-1.0,"confidence":0.0-1.0,'
        f'"thesis":"one sentence on your estimate","counterargument":"one sentence on the main risk"}}'
    )
    data = client.chat_json(PROB_SYS, user, fallback=fallback, fast=False)
    try:
        data["prob"] = max(0.01, min(0.99, float(data.get("prob", market_yes))))
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.3))))
    except (TypeError, ValueError):
        return fallback
    return data


# ── paper engine (probability-move trading) ───────────────────────────────

@dataclass
class PredPosition:
    market_id: str
    question: str
    side: str            # YES | NO
    entry_yes: float     # YES price at entry
    stake: float
    target_yes: float
    stop_yes: float
    entry_cycle: int
    entry_time: str
    est_prob: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PredEngine:
    def __init__(self) -> None:
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        default = {
            "balance": config.PRED_INITIAL_BALANCE,
            "equity": config.PRED_INITIAL_BALANCE,
            "peak_equity": config.PRED_INITIAL_BALANCE,
            "cycle": 0, "mandate_seq": 0,
            "open_positions": [], "last_yes": {},
            "closed_trades": 0, "wins": 0, "losses": 0,
        }
        state = store.read_json(config.PRED_PORTFOLIO_FILE, default)
        for k, v in default.items():
            state.setdefault(k, v)
        return state

    def save(self) -> None:
        store.write_json(config.PRED_PORTFOLIO_FILE, self.state)

    def _yes_value(self, pos: PredPosition, yes: float) -> float:
        """Current value of a stake given the YES price moved entry->yes."""
        if pos.side == "YES":
            return pos.stake * (yes / pos.entry_yes) if pos.entry_yes else pos.stake
        entry_no, cur_no = 1 - pos.entry_yes, 1 - yes
        return pos.stake * (cur_no / entry_no) if entry_no else pos.stake

    def _recompute_equity(self) -> None:
        last = self.state["last_yes"]
        val = 0.0
        for pd in self.state["open_positions"]:
            p = PredPosition(**pd)
            val += self._yes_value(p, last.get(p.market_id, p.entry_yes)) - p.stake
        self.state["equity"] = round(self.state["balance"] + val, 2)
        self.state["peak_equity"] = round(max(self.state["peak_equity"], self.state["equity"]), 2)

    def open(self, market: dict, side: str, est: dict, seq: int) -> dict[str, Any]:
        yes = market["yes_price"]
        stake = round(self.state["equity"] * config.PRED_SIZE_PCT, 2)
        if side == "YES":
            target, stop = est["prob"], max(0.01, yes - config.PRED_STOP_BAND)
        else:
            target, stop = est["prob"], min(0.99, yes + config.PRED_STOP_BAND)
        fee = stake * config.TAKER_FEE
        self.state["balance"] = round(self.state["balance"] - fee, 2)
        pos = PredPosition(
            market_id=market["id"], question=market["question"], side=side,
            entry_yes=yes, stake=stake, target_yes=round(target, 3), stop_yes=round(stop, 3),
            entry_cycle=self.state["cycle"], entry_time=_now(), est_prob=est["prob"],
        )
        self.state["open_positions"].append(pos.to_dict())
        self.state["last_yes"][market["id"]] = yes
        mandate_id = f"PM-{datetime.now(timezone.utc):%Y-%m-%d}-{seq:04d}"
        store.append_trade_log_to(config.PRED_TRADE_LOG_CSV, {
            "timestamp": pos.entry_time, "mandate_id": mandate_id, "market": pos.question,
            "side": side, "event": "OPEN", "yes_price": yes, "stake": stake,
            "est_prob": est["prob"], "pnl": 0.0,
        })
        logger.info(f"PRED OPEN {side} @ {yes} (est {est['prob']:.2f}) — {pos.question[:60]}")
        return pos.to_dict()

    def update_and_exit(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        for mid, yes in prices.items():
            self.state["last_yes"][mid] = yes
        closed, survivors = [], []
        for pd in self.state["open_positions"]:
            p = PredPosition(**pd)
            yes = prices.get(p.market_id, self.state["last_yes"].get(p.market_id, p.entry_yes))
            reason = self._exit_reason(p, yes)
            if reason is None:
                survivors.append(pd)
                continue
            closed.append(self._close(p, yes, reason))
        self.state["open_positions"] = survivors
        self._recompute_equity()
        return closed

    def _exit_reason(self, p: PredPosition, yes: float) -> str | None:
        held = self.state["cycle"] - p.entry_cycle
        if p.side == "YES":
            if yes >= p.target_yes:
                return "target"
            if yes <= p.stop_yes:
                return "stop"
        else:
            if yes <= p.target_yes:
                return "target"
            if yes >= p.stop_yes:
                return "stop"
        if held >= config.PRED_TIMEOUT_BARS:
            return "timeout"
        return None

    def _close(self, p: PredPosition, yes: float, reason: str) -> dict[str, Any]:
        value = self._yes_value(p, yes)
        pnl = round(value - p.stake, 2)
        self.state["balance"] = round(self.state["balance"] + value - p.stake, 2)
        self.state["closed_trades"] += 1
        win = pnl > 0
        self.state["wins"] += int(win)
        self.state["losses"] += int(not win)
        rec = {
            "market_id": p.market_id, "question": p.question, "side": p.side,
            "entry_yes": p.entry_yes, "exit_yes": yes, "stake": p.stake,
            "pnl": pnl, "pnl_pct": round(pnl / p.stake * 100, 2) if p.stake else 0,
            "reason": reason, "est_prob": p.est_prob, "win": win, "exit_time": _now(),
        }
        store.append_trade_log_to(config.PRED_TRADE_LOG_CSV, {
            "timestamp": rec["exit_time"], "mandate_id": "", "market": p.question,
            "side": p.side, "event": f"CLOSE_{reason.upper()}", "yes_price": yes,
            "stake": p.stake, "est_prob": p.est_prob, "pnl": pnl,
        })
        logger.info(f"PRED CLOSE {p.side} @ {yes} [{reason}] pnl={pnl} — {p.question[:50]}")
        return rec

    def next_seq(self) -> int:
        self.state["mandate_seq"] += 1
        return self.state["mandate_seq"]

    def held_market_ids(self) -> set[str]:
        return {p["market_id"] for p in self.state["open_positions"]}


# ── cycle ──────────────────────────────────────────────────────────────────

def run_cycle(engine: PredEngine) -> None:
    engine.state["cycle"] += 1
    markets = fetch_markets(config.PRED_MARKETS)
    # exits first (re-price held markets from the fresh fetch)
    prices = {m["id"]: m["yes_price"] for m in markets}
    engine.update_and_exit(prices)

    held = engine.held_market_ids()
    for m in markets:
        if m["id"] in held or len(engine.state["open_positions"]) >= config.PRED_MAX_POSITIONS:
            continue
        est = estimate_probability(m["question"], m["yes_price"])
        edge = est["prob"] - m["yes_price"]
        side = "YES" if edge > 0 else "NO"
        seq = engine.next_seq()

        decision = "REJECTED"
        if abs(edge) >= config.PRED_EDGE_THRESHOLD and est["confidence"] >= 0.45:
            decision = "APPROVED"

        record = {
            "mandate_id": f"PM-{datetime.now(timezone.utc):%Y-%m-%d}-{seq:04d}",
            "timestamp": _now(), "market": m["question"], "market_id": m["id"],
            "yes_price": m["yes_price"], "est_prob": round(est["prob"], 3),
            "edge": round(edge, 3), "side": side, "action": f"BUY_{side}" if decision == "APPROVED" else "NO_TRADE",
            "confidence": est["confidence"], "thesis": est["thesis"],
            "counterargument": est["counterargument"],
            "vault": {"decision": decision, "reason":
                      f"edge {edge:+.2f} vs threshold {config.PRED_EDGE_THRESHOLD}"},
            "equity": engine.state["equity"],
        }
        store.append_json_list(config.PRED_MANDATES_FILE, record, cap=1000)
        if decision == "APPROVED":
            engine.open(m, side, est, seq)

    engine._recompute_equity()
    engine.save()
    logger.info(f"PRED cycle {engine.state['cycle']} | open={len(engine.state['open_positions'])} "
                f"equity={engine.state['equity']}")


def run(cycles: int | None, interval: int = 0) -> None:
    store.ensure_dirs()
    engine = PredEngine()
    logger.info(f"VesperClaw PREDICTION mode | provider={config.LLM_PROVIDER} "
                f"markets={config.PRED_MARKETS}")
    n = 0
    while True:
        run_cycle(engine)
        n += 1
        if cycles and n >= cycles:
            break
        if interval:
            time.sleep(interval)
    s = engine.state
    closed = s["closed_trades"]
    wr = (s["wins"] / closed * 100) if closed else 0.0
    logger.info(f"PRED SUMMARY equity={s['equity']} trades={closed} win_rate={wr:.1f}%")


if __name__ == "__main__":
    run(cycles=1)
