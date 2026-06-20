"""Signal Mandate — the explainable, auditable record of every proposed action.

A mandate is generated whether or not a trade is taken. When the council declines,
a NO_TRADE mandate is still recorded so the audit trail shows *why* the agent stood
aside — refusals are first-class citizens here.

Each mandate carries the thesis AND its strongest counterargument (the adversarial
pass), plus ATR-derived stop-loss / take-profit and the full agent vote record.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any

import config
from vesperclaw.agents import CouncilResult
from vesperclaw.snapshot import Snapshot


def _new_mandate_id(seq: int) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"VC-{day}-{seq:04d}"


@dataclass
class Mandate:
    mandate_id: str
    timestamp: str
    symbol: str
    mode: str                 # always "paper"
    action: str               # LONG | SHORT | NO_TRADE
    regime: str
    regime_confidence: float
    confidence: float
    requested_size_pct: float
    entry_type: str           # market
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    stop_loss_pct: float | None
    take_profit_pct: float | None
    rr: float | None
    thesis: str
    counterargument: str
    invalidation: str
    leading_agent: str | None
    risk_veto: bool
    agent_votes: dict[str, str] = field(default_factory=dict)
    opinions: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_mandate(snap: Snapshot, council: CouncilResult, seq: int) -> Mandate:
    """Assemble a Signal Mandate from a snapshot + council decision."""
    mandate_id = _new_mandate_id(seq)
    price = snap.price
    atr = snap.atr

    action = "NO_TRADE"
    sl = tp = sl_pct = tp_pct = rr = None
    invalidation = "N/A — no position taken."

    if council.direction in ("long", "short"):
        action = "LONG" if council.direction == "long" else "SHORT"
        if council.direction == "long":
            sl = round(price - config.SL_ATR_MULT * atr, 2)
            tp = round(price + config.TP_ATR_MULT * atr, 2)
        else:
            sl = round(price + config.SL_ATR_MULT * atr, 2)
            tp = round(price - config.TP_ATR_MULT * atr, 2)
        sl_pct = round(abs(price - sl) / price * 100, 3)
        tp_pct = round(abs(tp - price) / price * 100, 3)
        rr = round(abs(tp - price) / abs(price - sl), 2) if (price - sl) else None
        invalidation = (
            f"Invalid if price hits stop {sl} "
            f"(~{sl_pct}% / {config.SL_ATR_MULT}x ATR) or regime flips away from {snap.regime}."
        )

    votes = {o.name: o.vote for o in council.opinions}

    return Mandate(
        mandate_id=mandate_id,
        timestamp=snap.timestamp,
        symbol=snap.symbol,
        mode="paper",
        action=action,
        regime=snap.regime,
        regime_confidence=snap.regime_confidence,
        confidence=council.confidence,
        requested_size_pct=council.requested_size_pct,
        entry_type="market",
        entry_price=price,
        stop_loss=sl,
        take_profit=tp,
        stop_loss_pct=sl_pct,
        take_profit_pct=tp_pct,
        rr=rr,
        thesis=council.thesis,
        counterargument=council.counterargument,
        invalidation=invalidation,
        leading_agent=council.leading_agent,
        risk_veto=council.risk_veto,
        agent_votes=votes,
        opinions=[o.to_dict() for o in council.opinions],
        snapshot=snap.to_dict(),
    )


if __name__ == "__main__":
    import json
    from vesperclaw.snapshot import build_snapshot
    from vesperclaw.agents import run_council

    config.DEMO_DATA = True
    snap = build_snapshot()
    council = run_council(snap)
    m = build_mandate(snap, council, seq=1)
    print(json.dumps(m.to_dict(), indent=2, default=str))
