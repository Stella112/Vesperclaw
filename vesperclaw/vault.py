"""AgentVault — the risk firewall every mandate must clear before execution.

No mandate executes on confidence alone. AgentVault enforces hard limits and can
DOWNSIZE, DELAY, or REJECT. It also records **Vault Saves**: when it blocks or
shrinks a trade, it stores the would-be entry so the system can later check whether
the block actually avoided a loss (reconciled in evolution.py / at trade close).

Decisions:
    APPROVED            — execute as requested
    APPROVED_DOWNSIZED  — execute at a reduced size
    DELAYED             — cooldown active; skip this cycle
    REJECTED            — blocked outright
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

import config
from vesperclaw import store
from vesperclaw.mandate import Mandate


@dataclass
class VaultDecision:
    mandate_id: str
    decision: str
    execution_allowed: bool
    requested_size_pct: float
    approved_size_pct: float
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate(mandate: Mandate, portfolio: dict[str, Any]) -> VaultDecision:
    """Run all risk checks against the current portfolio state."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    req = mandate.requested_size_pct

    # No-trade mandates never reach execution.
    if mandate.action == "NO_TRADE":
        return VaultDecision(
            mandate.mandate_id, "REJECTED", False, req, 0.0,
            "No actionable direction from council.", {"actionable": False},
        )

    # 1. paper-mode lock
    checks["paper_mode"] = mandate.mode == "paper"
    if not checks["paper_mode"]:
        reasons.append("not in paper mode")

    # 2. symbol allowlist
    checks["allowlisted_symbol"] = mandate.symbol in config.SYMBOL_ALLOWLIST
    if not checks["allowlisted_symbol"]:
        reasons.append(f"{mandate.symbol} not allowlisted")

    # 3. confidence floor
    checks["confidence_floor"] = mandate.confidence >= config.MIN_CONFIDENCE
    if not checks["confidence_floor"]:
        reasons.append(f"confidence {mandate.confidence} < {config.MIN_CONFIDENCE}")

    # 4. risk-agent / volatility veto
    checks["volatility_ok"] = not mandate.risk_veto
    if not checks["volatility_ok"]:
        reasons.append("risk veto / volatility above danger threshold")

    # 5. minimum risk/reward
    checks["min_rr"] = (mandate.rr or 0) >= config.MIN_RR
    if not checks["min_rr"]:
        reasons.append(f"R:R {mandate.rr} < {config.MIN_RR}")

    # 6. daily loss limit
    day_start = portfolio.get("day_start_equity", portfolio.get("equity", config.INITIAL_BALANCE))
    equity = portfolio.get("equity", config.INITIAL_BALANCE)
    daily_pl_pct = (equity - day_start) / day_start if day_start else 0
    checks["daily_loss_ok"] = daily_pl_pct > -config.MAX_DAILY_LOSS_PCT
    if not checks["daily_loss_ok"]:
        reasons.append(f"daily loss limit hit ({daily_pl_pct:.2%})")

    # 7. max drawdown -> lockdown
    peak = portfolio.get("peak_equity", config.INITIAL_BALANCE)
    drawdown = (equity - peak) / peak if peak else 0
    checks["drawdown_ok"] = drawdown > -config.MAX_DRAWDOWN_PCT
    if not checks["drawdown_ok"]:
        reasons.append(f"max drawdown breached ({drawdown:.2%}) — lockdown")

    # 8. position limits — portfolio-wide and per-symbol
    open_count = len(portfolio.get("open_positions", []))
    sym_count = portfolio.get("symbol_open_count", 0)
    checks["positions_ok"] = (
        open_count < config.MAX_OPEN_POSITIONS
        and sym_count < config.MAX_POSITIONS_PER_SYMBOL
    )
    if not checks["positions_ok"]:
        if sym_count >= config.MAX_POSITIONS_PER_SYMBOL:
            reasons.append(f"already holding {mandate.symbol}")
        else:
            reasons.append(f"max open positions ({open_count}/{config.MAX_OPEN_POSITIONS}) reached")

    # 9. portfolio exposure — correlated basket, same-direction positions add up
    direction = "long" if mandate.action == "LONG" else "short"
    positions = portfolio.get("open_positions", [])
    same_dir_exposure = sum(
        p.get("size_pct", 0) for p in positions if p.get("direction") == direction
    )
    exposure_room = config.MAX_PORTFOLIO_EXPOSURE_PCT - same_dir_exposure
    checks["portfolio_exposure_ok"] = exposure_room > 0.001
    if not checks["portfolio_exposure_ok"]:
        reasons.append(
            f"portfolio {direction} exposure cap reached "
            f"({same_dir_exposure:.0%}/{config.MAX_PORTFOLIO_EXPOSURE_PCT:.0%})"
        )

    # 10. cooldown (soft -> DELAYED rather than REJECTED)
    cycle = portfolio.get("cycle", 0)
    cooldown_until = portfolio.get("cooldown_until_cycle", 0)
    cooldown_active = cycle < cooldown_until
    checks["cooldown_ok"] = not cooldown_active

    # ── decide ──
    hard_fail = not all(
        checks[k] for k in (
            "paper_mode", "allowlisted_symbol", "confidence_floor",
            "volatility_ok", "min_rr", "daily_loss_ok", "drawdown_ok",
            "positions_ok", "portfolio_exposure_ok",
        )
    )

    if hard_fail:
        decision = VaultDecision(
            mandate.mandate_id, "REJECTED", False, req, 0.0,
            "; ".join(reasons) or "risk checks failed", checks,
        )
        _record_vault_save(mandate, decision)
        return decision

    if cooldown_active:
        return VaultDecision(
            mandate.mandate_id, "DELAYED", False, req, 0.0,
            f"cooldown active until cycle {cooldown_until}", checks,
        )

    # size cap -> possible downsize (per-trade cap AND remaining portfolio room)
    approved = min(req, config.MAX_POSITION_SIZE_PCT, exposure_room)
    if approved < req - 1e-9:
        capped_by = "portfolio exposure room" if exposure_room <= config.MAX_POSITION_SIZE_PCT \
            else f"max {config.MAX_POSITION_SIZE_PCT:.0%}"
        decision = VaultDecision(
            mandate.mandate_id, "APPROVED_DOWNSIZED", True, req, round(approved, 4),
            f"size reduced from {req:.2%} to {approved:.2%} ({capped_by})",
            checks,
        )
        _record_vault_save(mandate, decision, downsized=True)
        return decision

    return VaultDecision(
        mandate.mandate_id, "APPROVED", True, req, round(approved, 4),
        "all checks passed", checks,
    )


def _record_vault_save(mandate: Mandate, decision: VaultDecision, downsized: bool = False) -> None:
    """Log a blocked/shrunk trade so we can later check if blocking saved money."""
    store.append_json_list(
        config.VAULT_SAVES_FILE,
        {
            "mandate_id": mandate.mandate_id,
            "timestamp": mandate.timestamp,
            "symbol": mandate.symbol,
            "direction": mandate.action.lower(),
            "entry_price": mandate.entry_price,
            "stop_loss": mandate.stop_loss,
            "take_profit": mandate.take_profit,
            "decision": decision.decision,
            "downsized": downsized,
            "reason": decision.reason,
            "resolved": False,
            "would_be_pnl_pct": None,
            "verdict": None,  # "good_block" | "bad_block" once resolved
        },
        cap=500,
    )


if __name__ == "__main__":
    import json
    from vesperclaw.snapshot import build_snapshot
    from vesperclaw.agents import run_council
    from vesperclaw.mandate import build_mandate

    config.DEMO_DATA = True
    snap = build_snapshot()
    council = run_council(snap)
    m = build_mandate(snap, council, seq=1)
    pf = {"equity": 10000, "day_start_equity": 10000, "peak_equity": 10000,
          "open_positions": [], "cycle": 10, "cooldown_until_cycle": 0}
    print(json.dumps(evaluate(m, pf).to_dict(), indent=2))
