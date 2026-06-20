"""Qwen-powered analyst agents.

Division of labour that keeps the system auditable:
  * Python computes the *direction* deterministically from the snapshot signals
    (EMA crossover for trend, RSI+Bollinger for reversion). This is ground truth.
  * The LLM agents add *judgment*: a confidence score, a plain-English thesis, and
    — for the leading idea — the strongest counterargument (the adversarial pass).

The Regime Referee decides which strategy agent may lead:
  trend_up / trend_down -> Trend Agent
  range                 -> Mean-Reversion Agent
  uncertain             -> no lead (flat unless confidence clears a higher bar)

If the LLM is unavailable the agents fall back to deterministic heuristics, so the
loop always produces a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from loguru import logger

import config
from vesperclaw.llm_client import get_client
from vesperclaw.snapshot import Snapshot


@dataclass
class AgentOpinion:
    name: str
    vote: str            # approve | neutral | oppose
    direction: str | None  # long | short | None
    confidence: float    # 0..1
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CouncilResult:
    leading_agent: str | None
    direction: str | None
    confidence: float
    thesis: str
    counterargument: str
    risk_veto: bool
    requested_size_pct: float
    opinions: list[AgentOpinion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["opinions"] = [o.to_dict() for o in self.opinions]
        return d


# ── helpers ────────────────────────────────────────────────────────────

def _snap_brief(s: Snapshot) -> str:
    """Compact textual snapshot for the LLM prompt."""
    parts = [
        f"symbol={s.symbol} price={s.price} tf={s.timeframe}",
        f"regime={s.regime} (conf {s.regime_confidence})",
        f"ADX={s.adx} +DI={s.plus_di} -DI={s.minus_di}",
        f"EMA{config.EMA_FAST}={s.ema_fast} EMA{config.EMA_SLOW}={s.ema_slow}",
        f"RSI={s.rsi} BB[{s.bb_lower}/{s.bb_mid}/{s.bb_upper}]",
        f"ATR%={s.atr_pct} vol={s.volume_state} ret6={s.recent_return_pct}%",
        f"signals={s.signals}",
    ]
    if s.funding_rate is not None:
        parts.append(f"funding={s.funding_rate}")
    return " | ".join(parts)


def _ask_agent(role: str, system: str, snap: Snapshot, base_direction: str | None,
               base_conf: float, fast: bool = True) -> AgentOpinion:
    """Run one analyst agent; LLM supplies confidence + rationale, direction is fixed."""
    client = get_client()
    user = (
        f"Market snapshot:\n{_snap_brief(snap)}\n\n"
        f"Deterministic entry signal for your strategy: {base_direction or 'none'}\n"
        f"Respond ONLY with JSON: "
        f'{{"vote":"approve|neutral|oppose","confidence":0.0-1.0,'
        f'"thesis":"one sentence why this trade makes sense now",'
        f'"counterargument":"one sentence on the strongest risk to it"}}'
    )
    fallback = {
        "vote": "approve" if base_direction else "neutral",
        "confidence": base_conf,
        "thesis": f"{role}: deterministic signal = {base_direction or 'no entry'}.",
        "counterargument": "Signal may fail if regime shifts.",
    }
    data = client.chat_json(system, user, fallback=fallback, fast=fast)
    conf = float(data.get("confidence", base_conf))
    conf = max(0.0, min(1.0, conf))
    return AgentOpinion(
        name=role,
        vote=str(data.get("vote", fallback["vote"])),
        direction=base_direction,
        confidence=round(conf, 3),
        rationale=str(data.get("thesis", fallback["thesis"])),
    )


# ── individual agents ────────────────────────────────────────────────────

TREND_SYS = (
    "You are the Trend Agent in an autonomous crypto trading system. You only act in "
    "trending regimes and follow momentum via EMA crossovers. You are disciplined and "
    "concise. Never invent a direction; judge the one given."
)
REVERSION_SYS = (
    "You are the Mean-Reversion Agent. You act in ranging regimes, fading overextended "
    "moves using RSI and Bollinger Bands, expecting a return to the mean. Concise."
)
RISK_SYS = (
    "You are the Risk Agent. You protect capital. You assess volatility, drawdown and "
    "whether conditions are too dangerous to trade. You can veto. Concise."
)
ALLOCATOR_SYS = (
    "You are the Allocator Agent. Given confidence and risk budget you decide position "
    "size as a percentage of equity, conservative by default. Concise."
)


def _trend_opinion(snap: Snapshot) -> AgentOpinion:
    cross = snap.signals.get("trend_entry")          # long/short from a fresh EMA cross
    bias_long = snap.signals.get("ema_long_bias")
    conf = snap.regime_confidence

    # Trend-following only takes trades that AGREE with the regime direction.
    # A fresh crossover in-direction is strongest; otherwise lean on EMA bias.
    if snap.regime == "trend_up":
        if cross == "long":
            direction, base_conf = "long", 0.6 + 0.35 * conf
        elif bias_long:
            direction, base_conf = "long", 0.45 * conf
        else:
            direction, base_conf = None, 0.2
    elif snap.regime == "trend_down":
        if cross == "short":
            direction, base_conf = "short", 0.6 + 0.35 * conf
        elif not bias_long:
            direction, base_conf = "short", 0.45 * conf
        else:
            direction, base_conf = None, 0.2
    else:  # uncertain regime — take the raw cross if any (lower conviction)
        direction = cross
        base_conf = 0.6 + 0.35 * conf if direction else 0.2

    return _ask_agent("trend_agent", TREND_SYS, snap, direction, round(base_conf, 3))


def _reversion_opinion(snap: Snapshot) -> AgentOpinion:
    direction = snap.signals.get("reversion_entry")
    base_conf = 0.6 + 0.35 * snap.regime_confidence if direction else 0.2
    return _ask_agent("mean_reversion_agent", REVERSION_SYS, snap, direction, round(base_conf, 3))


def _risk_opinion(snap: Snapshot, proposed_direction: str | None) -> AgentOpinion:
    veto = snap.high_volatility
    base_conf = 0.2 if veto else 0.7
    client = get_client()
    user = (
        f"Market snapshot:\n{_snap_brief(snap)}\n\n"
        f"Proposed trade direction: {proposed_direction or 'none'}. "
        f"ATR% = {snap.atr_pct} (danger threshold {config.DANGER_VOLATILITY_PCT}).\n"
        f'Respond ONLY with JSON: {{"vote":"approve|neutral|oppose",'
        f'"confidence":0.0-1.0,"thesis":"risk read in one sentence",'
        f'"counterargument":"what could still go wrong"}}'
    )
    fallback = {
        "vote": "oppose" if veto else "approve",
        "confidence": base_conf,
        "thesis": (
            "Volatility above danger threshold — stand aside."
            if veto else "Volatility within tolerance; risk acceptable."
        ),
        "counterargument": "Volatility can spike intrabar regardless.",
    }
    data = client.chat_json(RISK_SYS, user, fallback=fallback, fast=True)
    return AgentOpinion(
        name="risk_agent",
        vote=str(data.get("vote", fallback["vote"])),
        direction=None,
        confidence=round(float(data.get("confidence", base_conf)), 3),
        rationale=str(data.get("thesis", fallback["thesis"])),
    )


def _allocator_size(confidence: float, risk_veto: bool) -> tuple[float, AgentOpinion]:
    """Size as % of equity notional, scaled by confidence, capped by config."""
    if risk_veto:
        size = 0.0
    else:
        size = config.MAX_POSITION_SIZE_PCT * confidence
        size = max(0.0, min(config.MAX_POSITION_SIZE_PCT, size))
    op = AgentOpinion(
        name="allocator_agent",
        vote="approve" if size > 0 else "oppose",
        direction=None,
        confidence=round(confidence, 3),
        rationale=f"Requested {round(size * 100, 2)}% of equity (conf {round(confidence, 2)}).",
    )
    return round(size, 4), op


# ── council orchestration with adversarial debate ────────────────────────

def run_council(snap: Snapshot, weights: dict[str, float] | None = None) -> CouncilResult:
    """Run the regime-gated council and return a fused decision with debate."""
    weights = weights or config.DEFAULT_WEIGHTS
    opinions: list[AgentOpinion] = []

    # 1. Regime referee picks the leading strategy agent.
    if snap.regime in ("trend_up", "trend_down"):
        lead = _trend_opinion(snap)
        opposing = _reversion_opinion(snap)
        leading_name = "trend_agent"
    elif snap.regime == "range":
        lead = _reversion_opinion(snap)
        opposing = _trend_opinion(snap)
        leading_name = "mean_reversion_agent"
    else:  # uncertain — both vote, but bar is higher and no automatic lead
        lead = _trend_opinion(snap)
        opposing = _reversion_opinion(snap)
        # pick whichever has a real entry + higher confidence
        if (opposing.direction and opposing.confidence > lead.confidence):
            lead, opposing = opposing, lead
        leading_name = lead.name

    opinions.extend([lead, opposing])

    direction = lead.direction
    # weight the leading agent's confidence by its learned weight in this regime
    lead_weight = weights.get(leading_name, 0.3)
    confidence = lead.confidence * (0.7 + 0.6 * lead_weight)  # weight nudges, doesn't dominate
    confidence = round(max(0.0, min(1.0, confidence)), 3)

    # 2. Risk agent assessment (may veto).
    risk = _risk_opinion(snap, direction)
    opinions.append(risk)
    risk_veto = risk.vote == "oppose" or snap.high_volatility

    # 3. Uncertain regime needs a higher confidence bar.
    min_conf = config.MIN_CONFIDENCE + (
        config.UNCERTAIN_CONFIDENCE_BONUS if snap.regime == "uncertain" else 0.0
    )
    if direction is None or confidence < min_conf:
        direction = None

    # 4. Allocator sizes the trade.
    size_pct, alloc = _allocator_size(confidence, risk_veto or direction is None)
    opinions.append(alloc)

    # 5. Thesis + adversarial counterargument.
    if direction:
        thesis = lead.rationale
        # strongest counter = opposing agent's view or risk agent's caution
        counterargument = (
            risk.rationale if risk_veto
            else (opposing.rationale or "Opposing strategy sees no edge here.")
        )
    else:
        thesis = "No trade: regime/confidence/risk conditions not met."
        counterargument = "Standing aside has opportunity cost if the move runs."

    return CouncilResult(
        leading_agent=leading_name if direction else None,
        direction=direction,
        confidence=confidence,
        thesis=thesis,
        counterargument=counterargument,
        risk_veto=risk_veto,
        requested_size_pct=size_pct if direction else 0.0,
        opinions=opinions,
    )


if __name__ == "__main__":
    import json
    from vesperclaw.snapshot import build_snapshot

    config.DEMO_DATA = True
    snap = build_snapshot()
    result = run_council(snap)
    print(f"regime={snap.regime} adx={snap.adx}")
    print(json.dumps(result.to_dict(), indent=2, default=str))
