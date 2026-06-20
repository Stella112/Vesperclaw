"""Tiny JSON/CSV persistence helpers shared by the engine, vault, and evolution.

State lives as flat files under DATA_DIR so the dashboard can read them and the
whole audit trail is plain, inspectable, git-friendly text.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any

import config


def ensure_dirs() -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)


def read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str, data: Any) -> None:
    ensure_dirs()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def append_json_list(path: str, item: Any, cap: int | None = None) -> None:
    """Append to a JSON array file, optionally keeping only the last `cap` items."""
    items = read_json(path, [])
    if not isinstance(items, list):
        items = []
    items.append(item)
    if cap and len(items) > cap:
        items = items[-cap:]
    write_json(path, items)


TRADE_LOG_HEADER = [
    "timestamp", "mandate_id", "symbol", "direction", "event",
    "price", "quantity", "notional", "fee",
    "balance_before", "balance_after", "pnl", "regime", "vault_decision",
]


def append_trade_log(row: dict[str, Any]) -> None:
    """Append one row to the required CSV trade log (creates header if missing)."""
    ensure_dirs()
    exists = os.path.exists(config.TRADE_LOG_CSV)
    with open(config.TRADE_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADER)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_LOG_HEADER})
