"""Bitget-compatible paper execution engine.

No live orders are ever placed. Fills are simulated at the current market price
with a realistic taker fee. The engine owns portfolio state, marks open positions
to market each cycle, and closes them on take-profit, stop-loss, or timeout.

Every fill (open and close) is written to the required CSV trade log with the
fields judges expect: timestamp, pair, direction, price, quantity, balance change,
PnL, plus the linked mandate id and vault decision.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger

import config
from vesperclaw import store
from vesperclaw.mandate import Mandate
from vesperclaw.vault import VaultDecision


@dataclass
class Position:
    mandate_id: str
    symbol: str
    direction: str          # long | short
    entry_price: float
    quantity: float
    notional: float
    size_pct: float
    stop_loss: float
    take_profit: float
    entry_cycle: int
    entry_time: str
    regime: str
    leading_agent: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperEngine:
    def __init__(self) -> None:
        self.state = self._load()

    # ── persistence ──
    def _load(self) -> dict[str, Any]:
        default = {
            "balance": config.INITIAL_BALANCE,
            "equity": config.INITIAL_BALANCE,
            "peak_equity": config.INITIAL_BALANCE,
            "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "day_start_equity": config.INITIAL_BALANCE,
            "cycle": 0,
            "cooldown_until_cycle": 0,
            "mandate_seq": 0,
            "open_positions": [],
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
        }
        state = store.read_json(config.PORTFOLIO_FILE, default)
        for k, v in default.items():
            state.setdefault(k, v)
        return state

    def save(self) -> None:
        store.write_json(config.PORTFOLIO_FILE, self.state)

    # ── accessors ──
    def next_seq(self) -> int:
        self.state["mandate_seq"] += 1
        return self.state["mandate_seq"]

    @property
    def positions(self) -> list[Position]:
        return [Position(**p) for p in self.state["open_positions"]]

    def portfolio_view(self) -> dict[str, Any]:
        """What the vault needs to make a decision."""
        return {
            "balance": self.state["balance"],
            "equity": self.state["equity"],
            "peak_equity": self.state["peak_equity"],
            "day_start_equity": self.state["day_start_equity"],
            "open_positions": self.state["open_positions"],
            "cycle": self.state["cycle"],
            "cooldown_until_cycle": self.state["cooldown_until_cycle"],
        }

    # ── lifecycle ──
    def begin_cycle(self, price: float) -> None:
        self.state["cycle"] += 1
        # daily rollover
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.state["day"]:
            self.state["day"] = today
            self.state["day_start_equity"] = self.state["equity"]
        self._mark_to_market(price)

    def _unrealized(self, price: float) -> float:
        pnl = 0.0
        for p in self.positions:
            if p.direction == "long":
                pnl += (price - p.entry_price) * p.quantity
            else:
                pnl += (p.entry_price - price) * p.quantity
        return pnl

    def _mark_to_market(self, price: float) -> None:
        self.state["equity"] = round(self.state["balance"] + self._unrealized(price), 2)
        self.state["peak_equity"] = round(max(self.state["peak_equity"], self.state["equity"]), 2)

    # ── execution ──
    def open_position(self, mandate: Mandate, vault: VaultDecision, price: float) -> Position:
        size_pct = vault.approved_size_pct
        notional = self.state["equity"] * size_pct
        qty = notional / price if price else 0.0
        fee = notional * config.TAKER_FEE
        direction = "long" if mandate.action == "LONG" else "short"

        bal_before = self.state["balance"]
        self.state["balance"] = round(bal_before - fee, 2)

        pos = Position(
            mandate_id=mandate.mandate_id,
            symbol=mandate.symbol,
            direction=direction,
            entry_price=price,
            quantity=round(qty, 8),
            notional=round(notional, 2),
            size_pct=size_pct,
            stop_loss=mandate.stop_loss,
            take_profit=mandate.take_profit,
            entry_cycle=self.state["cycle"],
            entry_time=_now(),
            regime=mandate.regime,
            leading_agent=mandate.leading_agent,
        )
        self.state["open_positions"].append(pos.to_dict())
        self._mark_to_market(price)

        store.append_trade_log({
            "timestamp": pos.entry_time, "mandate_id": pos.mandate_id, "symbol": pos.symbol,
            "direction": direction, "event": "OPEN", "price": price, "quantity": pos.quantity,
            "notional": pos.notional, "fee": round(fee, 4),
            "balance_before": bal_before, "balance_after": self.state["balance"],
            "pnl": 0.0, "regime": pos.regime, "vault_decision": vault.decision,
        })
        logger.info(f"OPEN {direction} {pos.symbol} @ {price} size {size_pct:.2%} ({pos.mandate_id})")
        return pos

    def check_exits(self, price: float) -> list[dict[str, Any]]:
        """Close positions hitting TP/SL/timeout. Returns closed-trade records."""
        closed: list[dict[str, Any]] = []
        survivors: list[dict[str, Any]] = []
        for pd in self.state["open_positions"]:
            p = Position(**pd)
            reason = self._exit_reason(p, price)
            if reason is None:
                survivors.append(pd)
                continue
            closed.append(self._close(p, price, reason))
        self.state["open_positions"] = survivors
        return closed

    def _exit_reason(self, p: Position, price: float) -> str | None:
        bars_held = self.state["cycle"] - p.entry_cycle
        if p.direction == "long":
            if price <= p.stop_loss:
                return "stop_loss"
            if price >= p.take_profit:
                return "take_profit"
        else:
            if price >= p.stop_loss:
                return "stop_loss"
            if price <= p.take_profit:
                return "take_profit"
        if bars_held >= config.TIMEOUT_BARS:
            return "timeout"
        return None

    def _close(self, p: Position, price: float, reason: str) -> dict[str, Any]:
        if p.direction == "long":
            gross = (price - p.entry_price) * p.quantity
        else:
            gross = (p.entry_price - price) * p.quantity
        exit_fee = (p.quantity * price) * config.TAKER_FEE
        net = gross - exit_fee

        bal_before = self.state["balance"]
        self.state["balance"] = round(bal_before + net, 2)
        self._mark_to_market(price)

        self.state["closed_trades"] += 1
        win = net > 0
        self.state["wins"] += int(win)
        self.state["losses"] += int(not win)
        # cooldown after each close
        self.state["cooldown_until_cycle"] = self.state["cycle"] + config.COOLDOWN_BARS

        pnl_pct = (net / p.notional * 100) if p.notional else 0.0
        record = {
            "mandate_id": p.mandate_id, "symbol": p.symbol, "direction": p.direction,
            "entry_price": p.entry_price, "exit_price": price, "quantity": p.quantity,
            "notional": p.notional, "pnl": round(net, 2), "pnl_pct": round(pnl_pct, 3),
            "reason": reason, "regime": p.regime, "leading_agent": p.leading_agent,
            "bars_held": self.state["cycle"] - p.entry_cycle, "win": win,
            "entry_time": p.entry_time, "exit_time": _now(),
        }
        store.append_trade_log({
            "timestamp": record["exit_time"], "mandate_id": p.mandate_id, "symbol": p.symbol,
            "direction": p.direction, "event": f"CLOSE_{reason.upper()}", "price": price,
            "quantity": p.quantity, "notional": p.notional, "fee": round(exit_fee, 4),
            "balance_before": bal_before, "balance_after": self.state["balance"],
            "pnl": round(net, 2), "regime": p.regime, "vault_decision": "",
        })
        store.append_json_list(config.ORDERS_FILE, record, cap=1000)
        logger.info(f"CLOSE {p.direction} {p.symbol} @ {price} [{reason}] pnl={net:.2f} ({p.mandate_id})")
        return record


if __name__ == "__main__":
    eng = PaperEngine()
    print("loaded portfolio:", eng.portfolio_view())
