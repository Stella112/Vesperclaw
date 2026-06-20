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
            "last_prices": {},
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

    def portfolio_view(self, symbol: str | None = None) -> dict[str, Any]:
        """What the vault needs to make a decision (optionally per-symbol)."""
        positions = self.state["open_positions"]
        return {
            "balance": self.state["balance"],
            "equity": self.state["equity"],
            "peak_equity": self.state["peak_equity"],
            "day_start_equity": self.state["day_start_equity"],
            "open_positions": positions,
            "symbol_open_count": sum(1 for p in positions if p.get("symbol") == symbol),
            "cycle": self.state["cycle"],
            "cooldown_until_cycle": self.state["cooldown_until_cycle"],
        }

    # ── lifecycle ──
    def begin_cycle(self, price: float | None = None) -> None:
        """Advance the cycle counter + handle daily rollover.

        In single-asset use pass `price` to also mark-to-market. In multi-asset
        use, call this once with no price, then `mark_prices(price_map)` after
        processing every symbol.
        """
        self.state["cycle"] += 1
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.state["day"]:
            self.state["day"] = today
            self.state["day_start_equity"] = self.state["equity"]
        if price is not None:
            self._mark_to_market(price)

    def _recompute_equity(self) -> None:
        """Equity = cash balance + unrealized PnL, each position priced by the
        last seen price for its own symbol (state['last_prices'])."""
        last = self.state.setdefault("last_prices", {})
        pnl = 0.0
        for p in self.positions:
            px = last.get(p.symbol, p.entry_price)
            pnl += (px - p.entry_price) * p.quantity if p.direction == "long" \
                else (p.entry_price - px) * p.quantity
        self.state["equity"] = round(self.state["balance"] + pnl, 2)
        self.state["peak_equity"] = round(max(self.state["peak_equity"], self.state["equity"]), 2)

    def update_price(self, symbol: str, price: float) -> None:
        """Record the latest price for a symbol (used before exits/sizing)."""
        self.state.setdefault("last_prices", {})[symbol] = price

    def mark_prices(self, price_map: dict[str, float]) -> None:
        """Mark the whole portfolio to market using a {symbol: price} map."""
        for sym, px in price_map.items():
            self.state.setdefault("last_prices", {})[sym] = px
        self._recompute_equity()

    def _mark_to_market(self, price: float) -> None:
        """Single-symbol convenience mark (single-asset path / open / close)."""
        self._recompute_equity()

    # ── execution ──
    def open_position(self, mandate: Mandate, vault: VaultDecision, price: float) -> Position:
        self.update_price(mandate.symbol, price)
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

    def check_exits(self, price: float, symbol: str | None = None) -> list[dict[str, Any]]:
        """Close positions hitting TP/SL/timeout. If `symbol` is given, only that
        symbol's positions are evaluated (at `price`); others are left untouched."""
        if symbol is not None:
            self.update_price(symbol, price)
        closed: list[dict[str, Any]] = []
        survivors: list[dict[str, Any]] = []
        for pd in self.state["open_positions"]:
            p = Position(**pd)
            if symbol is not None and p.symbol != symbol:
                survivors.append(pd)
                continue
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

        self.update_price(p.symbol, price)
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
