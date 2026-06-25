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
FOOTBALL_TERMS = (
    "football", "soccer", "premier league", "champions league", "europa league",
    "euro ", "uefa", "epl", "la liga", "serie a",
    "bundesliga", "nfl", "super bowl", "college football",
)
WORLD_CUP_TERMS = (
    "world cup", "worldcup", "fifa world cup", "world cup 2026", "fifa 2026",
)
WORLD_CUP_COUNTRIES = (
    "argentina", "brazil", "france", "england", "spain", "germany", "portugal",
    "netherlands", "usa", "united states", "mexico", "canada", "uruguay",
    "italy", "croatia", "morocco", "belgium", "switzerland", "japan",
    "south korea", "korea republic", "australia", "colombia", "ecuador",
)
WORLD_CUP_PLAYER_TERMS = (
    "golden boot", "golden ball", "golden glove", "score a goal", "1+ goals",
    "2+ goals", "goal contributions", "most goal contributions", "shots",
    "saves", "assists", "mbappe", "messi", "haaland", "vinicius",
    "bellingham", "kane", "neymar", "ronaldo", "yamal",
)
WORLD_CUP_WINNER_TERMS = (
    "win the 2026 fifa world cup", "win the world cup", "world cup champion",
    "champion be a nation", "reach the quarterfinals", "reach the semifinals",
    "reach the final", "be eliminated",
)
WORLD_CUP_FIXTURE_TERMS = (
    "scotland v brazil", "scotland vs brazil", "brazil beat scotland",
    "morocco v haiti", "morocco vs haiti", "morocco beat haiti",
    "switzerland v canada", "switzerland vs canada", "canada beat switzerland",
    "bosnia v qatar", "bosnia vs qatar", "qatar beat bosnia",
    "czechia v mexico", "czechia vs mexico", "mexico beat czechia",
    "south africa v korea", "south africa vs korea", "korea republic",
    "ecuador v germany", "ecuador vs germany", "germany beat ecuador",
    "turkiye v usa", "turkiye vs usa", "usa beat turkiye",
    "paraguay v australia", "paraguay vs australia",
    "colombia v portugal", "colombia vs portugal", "portugal beat colombia",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_yes_price(raw: Any) -> float | None:
    prices = raw
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except json.JSONDecodeError:
            return None
    if not prices:
        return None
    try:
        yes = float(prices[0])
    except (TypeError, ValueError, IndexError):
        return None
    if 0.0001 <= yes <= 0.9999:
        return yes
    return None


def _market_blob(market: dict[str, Any]) -> str:
    parts = [
        str(market.get("question", "")),
        str(market.get("slug", "")),
        str(market.get("category", "")),
        str(market.get("description", "")),
        str(market.get("groupItemTitle", "")),
    ]
    tags = market.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                parts.append(str(tag.get("label") or tag.get("name") or tag.get("slug") or ""))
            else:
                parts.append(str(tag))
    events = market.get("events") or []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            parts.extend(
                str(event.get(key, ""))
                for key in ("title", "ticker", "slug", "description")
            )
    return " ".join(parts).lower()


def market_topic(market: dict[str, Any]) -> str:
    blob = _market_blob(market)
    if (
        any(term in blob for term in WORLD_CUP_TERMS)
        or any(term in blob for term in WORLD_CUP_FIXTURE_TERMS)
        or (
            any(country in blob for country in WORLD_CUP_COUNTRIES)
            and any(term in blob for term in ("group", "score", "goal", "shots", "match", "beat", "vs.", " vs "))
        )
    ):
        return "world_cup"
    return "football" if any(term in blob for term in FOOTBALL_TERMS) else "general"


def market_kind(market: dict[str, Any]) -> str:
    blob = _market_blob(market)
    if market_topic(market) != "world_cup":
        return "football" if any(term in blob for term in FOOTBALL_TERMS) else "general"
    if any(term in blob for term in WORLD_CUP_PLAYER_TERMS):
        return "player_prop"
    if any(term in blob for term in WORLD_CUP_WINNER_TERMS):
        return "country_winner"
    if any(term in blob for term in WORLD_CUP_FIXTURE_TERMS) or any(
        term in blob for term in (" vs ", " vs.", " v ", "exact score", "o/u", "handicap", "to score first", "beat ")
    ):
        return "match_prop"
    return "world_cup"


def _market_record(m: dict[str, Any], yes: float, topic: str) -> dict[str, Any]:
    return {
        "id": str(m.get("id")),
        "question": m.get("question", "")[:160],
        "yes_price": round(yes, 3),
        "volume": float(m.get("volume") or 0),
        "volume24hr": float(m.get("volume24hr") or m.get("volume24hrClob") or 0),
        "topic": topic,
        "market_kind": market_kind(m),
        "updated_at": m.get("updatedAt") or m.get("updated_at"),
    }


def _world_cup_priority(market: dict[str, Any]) -> tuple[int, float]:
    kind_rank = {"country_winner": 0, "player_prop": 1, "match_prop": 2, "world_cup": 3}
    return (kind_rank.get(market.get("market_kind", "world_cup"), 4), -float(market.get("volume") or 0))


# ── market data ──────────────────────────────────────────────────────────

def fetch_markets(limit: int, topic: str = "general") -> list[dict[str, Any]]:
    """Return liquid binary markets: {id, question, yes_price, volume, topic}."""
    if limit <= 0:
        return []
    try:
        fetch_limit = limit * 4 if topic == "general" else max(limit * 120, 1000)
        r = requests.get(
            GAMMA_URL,
            params={"closed": "false", "active": "true", "order": "volume",
                    "ascending": "false", "limit": fetch_limit},
            timeout=10,
        )
        out = []
        for m in r.json():
            detected_topic = market_topic(m)
            if topic != "general" and detected_topic != topic:
                continue
            yes = _parse_yes_price(m.get("outcomePrices"))
            if yes is None:
                continue
            if topic == "world_cup":
                if 0.001 <= yes <= 0.999:
                    out.append(_market_record(m, yes, detected_topic))
            elif 0.05 < yes < 0.95:
                out.append(_market_record(m, yes, detected_topic))
            if topic != "world_cup" and len(out) >= limit:
                break
        if topic == "world_cup":
            out.sort(key=_world_cup_priority)
            out = out[:limit]
        if out:
            logger.info(f"Polymarket: {len(out)} live {topic} markets fetched.")
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Polymarket {topic} fetch failed ({e}); using synthetic markets.")
    return _synthetic_markets(limit, topic=topic)


def fetch_prediction_universe() -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    if config.PRED_INCLUDE_WORLD_CUP and config.PRED_WORLD_CUP_MARKETS > 0:
        markets.extend(fetch_markets(config.PRED_WORLD_CUP_MARKETS, topic="world_cup"))
    if config.PRED_INCLUDE_FOOTBALL and config.PRED_FOOTBALL_MARKETS > 0:
        markets.extend(fetch_markets(config.PRED_FOOTBALL_MARKETS, topic="football"))
    markets.extend(fetch_markets(config.PRED_MARKETS, topic="general"))

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for market in markets:
        if market["id"] in seen:
            continue
        seen.add(market["id"])
        unique.append(market)
    return unique


def fetch_market_yes(market_id: str) -> float | None:
    """Best-effort single-market refresh for open positions."""
    if market_id.startswith("SYN-"):
        return None
    try:
        r = requests.get(f"{GAMMA_URL}/{market_id}", timeout=10)
        r.raise_for_status()
        return _parse_yes_price(r.json().get("outcomePrices"))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Polymarket reprice skipped for {market_id}: {e}")
        return None


def _synthetic_markets(limit: int, topic: str = "general") -> list[dict[str, Any]]:
    import numpy as np
    rng = np.random.default_rng(int(time.time()) % 9999)
    general_qs = [
        "Will BTC close above $80k this month?",
        "Will the Fed cut rates at the next meeting?",
        "Will ETH flip its prior all-time high this quarter?",
        "Will a spot SOL ETF be approved this year?",
        "Will total crypto market cap exceed $3T this month?",
        "Will US CPI come in below forecast next print?",
        "Will a major exchange list this memecoin this week?",
        "Will gas fees on Ethereum spike above 100 gwei this week?",
    ]
    football_qs = [
        "Will Arsenal win their next Premier League match?",
        "Will Real Madrid win their next Champions League match?",
        "Will an NFL team score 30+ points in the next featured game?",
        "Will both teams score in the next major football final?",
        "Will the favorite win the next listed football market?",
        "Will a Premier League match finish with over 2.5 goals?",
    ]
    world_cup_qs = [
        "Will Brazil win the 2026 FIFA World Cup?",
        "Will Argentina win the 2026 FIFA World Cup?",
        "Will France win the 2026 FIFA World Cup?",
        "Will England win the 2026 FIFA World Cup?",
        "Will Kylian Mbappe win the Golden Boot at the 2026 FIFA World Cup?",
        "Will Lionel Messi score a goal at the 2026 FIFA World Cup?",
        "Will Brazil beat Scotland in their 2026 FIFA World Cup group match?",
        "Will Morocco beat Haiti in their 2026 FIFA World Cup group match?",
        "Will Canada beat Switzerland in their 2026 FIFA World Cup group match?",
        "Will Mexico beat Czechia in their 2026 FIFA World Cup group match?",
        "Will South Africa beat Korea Republic in their 2026 FIFA World Cup group match?",
        "Will Germany beat Ecuador in their next 2026 FIFA World Cup group match?",
        "Will the USA beat Turkiye in their next 2026 FIFA World Cup group match?",
        "Will Argentina win their next 2026 FIFA World Cup group match?",
    ]
    qs = world_cup_qs if topic == "world_cup" else football_qs if topic == "football" else general_qs
    return [
        {"id": f"SYN-{topic.upper()}-{i}", "question": qs[i % len(qs)],
         "yes_price": round(float(rng.uniform(0.2, 0.8)), 3),
         "volume": float(rng.uniform(1e4, 1e6)), "volume24hr": float(rng.uniform(500, 50000)),
         "topic": topic, "market_kind": market_kind({"question": qs[i % len(qs)]}),
         "updated_at": _now()}
        for i in range(min(limit, len(qs)))
    ]


# ── probability agent ─────────────────────────────────────────────────────

PROB_SYS = (
    "You are the Probability Agent for a prediction-market trader. Given a market "
    "question and the market's implied probability, estimate the TRUE probability of "
    "YES using base rates and reasoning. Be calibrated and honest; if you have no "
    "edge, return a probability close to the market's. Concise."
)


def estimate_probability(question: str, market_yes: float, topic: str = "general") -> dict[str, Any]:
    """Return {prob, confidence, thesis, counterargument}. Falls back to no-edge."""
    fallback = _heuristic_probability(question, market_yes, topic)
    if not config.PRED_USE_LLM:
        return {**fallback, "_source": "heuristic_fast"}
    client = get_client()
    user = (
        f"Market topic: {topic}\n"
        f"Market question: {question}\n"
        f"Market-implied P(YES) = {market_yes:.2f}\n"
        "If this is football/soccer/NFL or the 2026 FIFA World Cup, be extra calibrated: "
        "sports lines are efficient, so only claim edge when the question has a clear "
        "base-rate, tournament-context, or market-pricing reason.\n"
        f'Respond ONLY with JSON: {{"prob":0.0-1.0,"confidence":0.0-1.0,'
        f'"thesis":"one sentence on your estimate","counterargument":"one sentence on the main risk"}}'
    )
    data = client.chat_json(PROB_SYS, user, fallback=fallback, fast=True)
    try:
        data["prob"] = max(0.01, min(0.99, float(data.get("prob", market_yes))))
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.3))))
    except (TypeError, ValueError):
        return fallback
    return data


def _heuristic_probability(question: str, market_yes: float, topic: str) -> dict[str, Any]:
    """Fast calibrated estimate for live refreshes when LLM calls are too slow."""
    q = question.lower()
    kind = market_kind({"question": question})
    prob = market_yes
    confidence = 0.55
    thesis = "Market-implied odds are the anchor; no strong independent edge is assumed."
    counter = "Sports and prediction markets are efficient, so most apparent edges are noise."

    if topic == "world_cup":
        confidence = 0.62
        if kind == "country_winner":
            if market_yes < 0.02:
                prob = max(0.001, market_yes * 0.85)
                thesis = "Long-shot outright winner markets are usually efficiently priced and often too optimistic."
            elif any(team in q for team in ("brazil", "france", "england", "spain", "argentina")):
                prob = min(0.99, market_yes + 0.005)
                thesis = "Elite-team outright markets have plausible tournament paths, but the edge is still thin."
            else:
                prob = max(0.001, market_yes - 0.004)
                thesis = "Country path risk makes this a watchlist market, not a conviction trade."
        elif kind == "player_prop":
            confidence = 0.58
            if any(term in q for term in ("score a goal", "1+ shots", "shots")):
                prob = min(0.99, market_yes + 0.01)
                thesis = "Player prop has a concrete participation/stat path, but lineup risk remains material."
            else:
                prob = max(0.001, market_yes - 0.003)
                thesis = "Tournament awards and cumulative props carry rotation and bracket-path uncertainty."
        elif kind == "match_prop":
            confidence = 0.60
            prob = market_yes + (0.008 if "beat" in q else 0.0)
            thesis = "Match prop is concrete enough to track live, but not enough for a high-accuracy entry."

    elif topic == "football":
        confidence = 0.56
        prob = market_yes
        thesis = "Football market is tracked, but without a clear line-specific edge the agent stays near market odds."

    prob = round(max(0.001, min(0.999, prob)), 3)
    edge = prob - market_yes
    return {
        "prob": prob,
        "confidence": round(confidence, 2),
        "thesis": thesis,
        "counterargument": counter if abs(edge) < 0.03 else "The adjustment is small and can be overwhelmed by late news or lineup changes.",
    }


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
    confidence: float = 0.0
    edge: float = 0.0
    mandate_id: str = ""
    topic: str = "general"
    market_kind: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prediction_accuracy() -> dict[str, Any]:
    orders = store.read_json(config.PRED_ORDERS_FILE, [])
    if not isinstance(orders, list):
        orders = []
    closed = len(orders)
    wins = sum(1 for o in orders if o.get("win"))
    accuracy = wins / closed if closed else 0.0
    pnl = round(sum(float(o.get("pnl", 0.0)) for o in orders), 2)
    return {
        "closed": closed,
        "wins": wins,
        "losses": max(0, closed - wins),
        "accuracy": accuracy,
        "accuracy_pct": round(accuracy * 100, 1),
        "target_pct": round(config.PRED_TARGET_ACCURACY * 100, 1),
        "net_pnl": pnl,
    }


def high_accuracy_gate(edge: float, confidence: float) -> tuple[bool, list[str], dict[str, Any]]:
    """Return whether a prediction mandate clears the 90%-target gate."""
    stats = prediction_accuracy()
    min_edge = config.PRED_EDGE_THRESHOLD
    min_conf = config.PRED_MIN_CONFIDENCE
    if stats["closed"] >= 5 and stats["accuracy"] < config.PRED_TARGET_ACCURACY:
        # If realized accuracy lags the target, become more selective.
        shortfall = min(config.PRED_TARGET_ACCURACY - stats["accuracy"], 0.20)
        min_edge += shortfall * 0.25
        min_conf += shortfall * 0.25

    reasons: list[str] = []
    if abs(edge) < min_edge:
        reasons.append(f"edge {edge:+.2f} below required {min_edge:.2f}")
    if confidence < min_conf:
        reasons.append(f"confidence {confidence:.2f} below required {min_conf:.2f}")
    if not reasons:
        reasons.append(f"edge {edge:+.2f} and confidence {confidence:.2f} cleared high-accuracy gate")

    return not any("below required" in r for r in reasons), reasons, {
        "target_accuracy": config.PRED_TARGET_ACCURACY,
        "observed_accuracy": stats["accuracy"],
        "min_edge": round(min_edge, 3),
        "min_confidence": round(min_conf, 3),
        "sample_size": stats["closed"],
    }


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
        mandate_id = f"PM-{datetime.now(timezone.utc):%Y-%m-%d}-{seq:04d}"
        pos = PredPosition(
            market_id=market["id"], question=market["question"], side=side,
            entry_yes=yes, stake=stake, target_yes=round(target, 3), stop_yes=round(stop, 3),
            entry_cycle=self.state["cycle"], entry_time=_now(), est_prob=est["prob"],
            confidence=est.get("confidence", 0.0), edge=est.get("edge", est["prob"] - yes),
            mandate_id=mandate_id, topic=market.get("topic", "general"),
            market_kind=market.get("market_kind", market.get("topic", "general")),
        )
        self.state["open_positions"].append(pos.to_dict())
        self.state["last_yes"][market["id"]] = yes
        store.append_trade_log_to(config.PRED_TRADE_LOG_CSV, {
            "timestamp": pos.entry_time, "mandate_id": mandate_id, "market": pos.question,
            "topic": pos.topic, "market_kind": pos.market_kind, "side": side, "event": "OPEN",
            "yes_price": yes, "stake": stake,
            "est_prob": est["prob"], "confidence": pos.confidence, "edge": pos.edge, "pnl": 0.0,
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
            "mandate_id": p.mandate_id, "market_id": p.market_id, "topic": p.topic,
            "market_kind": p.market_kind,
            "question": p.question, "side": p.side,
            "entry_yes": p.entry_yes, "exit_yes": yes, "stake": p.stake,
            "pnl": pnl, "pnl_pct": round(pnl / p.stake * 100, 2) if p.stake else 0,
            "reason": reason, "est_prob": p.est_prob, "confidence": p.confidence,
            "edge": p.edge, "win": win, "entry_time": p.entry_time, "exit_time": _now(),
        }
        store.append_json_list(config.PRED_ORDERS_FILE, rec, cap=1000)
        store.append_trade_log_to(config.PRED_TRADE_LOG_CSV, {
            "timestamp": rec["exit_time"], "mandate_id": p.mandate_id, "market": p.question,
            "topic": p.topic, "market_kind": p.market_kind, "side": p.side,
            "event": f"CLOSE_{reason.upper()}", "yes_price": yes,
            "stake": p.stake, "est_prob": p.est_prob, "confidence": p.confidence,
            "edge": p.edge, "pnl": pnl,
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
    markets = fetch_prediction_universe()
    # exits first (re-price held markets from the fresh fetch)
    prices = {m["id"]: m["yes_price"] for m in markets}
    for market_id in engine.held_market_ids() - set(prices):
        yes = fetch_market_yes(market_id)
        if yes is not None:
            prices[market_id] = round(yes, 3)
    engine.update_and_exit(prices)

    held = engine.held_market_ids()
    evals = 0
    for m in markets:
        if m["id"] in held or len(engine.state["open_positions"]) >= config.PRED_MAX_POSITIONS:
            continue
        if evals >= config.PRED_MAX_EVALS_PER_CYCLE:
            break
        evals += 1
        est = estimate_probability(m["question"], m["yes_price"], topic=m.get("topic", "general"))
        edge = est["prob"] - m["yes_price"]
        est["edge"] = edge
        side = "YES" if edge > 0 else "NO"
        seq = engine.next_seq()

        approved, gate_reasons, gate = high_accuracy_gate(edge, est["confidence"])
        decision = "APPROVED" if approved else "REJECTED"

        record = {
            "mandate_id": f"PM-{datetime.now(timezone.utc):%Y-%m-%d}-{seq:04d}",
            "timestamp": _now(), "market": m["question"], "market_id": m["id"],
            "topic": m.get("topic", "general"),
            "market_kind": m.get("market_kind", m.get("topic", "general")),
            "yes_price": m["yes_price"], "est_prob": round(est["prob"], 3),
            "edge": round(edge, 3), "side": side, "action": f"BUY_{side}" if decision == "APPROVED" else "NO_TRADE",
            "confidence": est["confidence"], "thesis": est["thesis"],
            "counterargument": est["counterargument"],
            "vault": {
                "decision": decision,
                "reason": "; ".join(gate_reasons),
                "target_accuracy": gate["target_accuracy"],
                "observed_accuracy": gate["observed_accuracy"],
                "min_edge": gate["min_edge"],
                "min_confidence": gate["min_confidence"],
                "sample_size": gate["sample_size"],
            },
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
                f"markets={config.PRED_MARKETS} "
                f"world_cup={config.PRED_WORLD_CUP_MARKETS if config.PRED_INCLUDE_WORLD_CUP else 0} "
                f"football={config.PRED_FOOTBALL_MARKETS if config.PRED_INCLUDE_FOOTBALL else 0}")
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
    acc = prediction_accuracy()
    logger.info(
        f"PRED SUMMARY equity={s['equity']} trades={closed} win_rate={wr:.1f}% "
        f"accuracy_target={acc['target_pct']:.1f}% observed={acc['accuracy_pct']:.1f}%"
    )


if __name__ == "__main__":
    run(cycles=1)
