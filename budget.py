"""Darwin LLM Budget Circuit Breaker — prevents runaway spend.

Tracks per-call token usage, persists a monthly ledger, and blocks LLM
calls when the configured limit is reached.

Default limit: $50/mo via DARWIN_BUDGET_USD env var.
Ledger path: /tmp/darwin-budget.json (override via DARWIN_BUDGET_PATH).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

BUDGET_PATH = os.environ.get("DARWIN_BUDGET_PATH", "/tmp/darwin-budget.json")

# Static price table: (input_per_mtok_usd, output_per_mtok_usd)
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    "gemini-flash": (0.0, 0.0),      # free tier
    "gemini-2.5-flash": (0.0, 0.0),  # free tier
    "gemini-pro": (1.25, 5.0),
    "claude-opus": (15.0, 75.0),
    "claude-opus-4-7": (15.0, 75.0),
    "anthropic-sonnet": (3.0, 15.0),
    "claude-sonnet": (3.0, 15.0),
    "default": (3.0, 15.0),
}


def _price_for(provider: str) -> tuple[float, float]:
    provider_lc = provider.lower()
    for key, prices in _PRICE_TABLE.items():
        if key in provider_lc:
            return prices
    return _PRICE_TABLE["default"]


def _current_month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


class BudgetLedger:
    """Persistent monthly token/spend ledger."""

    def __init__(self, path: str = BUDGET_PATH) -> None:
        self._path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    def record_call(self, provider: str, tokens_in: int, tokens_out: int) -> None:
        """Record a single LLM call and persist."""
        month = _current_month()
        if month not in self._data:
            self._data[month] = {"calls": [], "total_usd": 0.0}
        price_in, price_out = _price_for(provider)
        cost = (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out
        self._data[month]["calls"].append({
            "provider": provider,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self._data[month]["total_usd"] = self._data[month].get("total_usd", 0.0) + cost
        self._save()

    def month_spend_usd(self, month: str | None = None) -> float:
        """Return running total spend for the given month (default: current)."""
        m = month or _current_month()
        return self._data.get(m, {}).get("total_usd", 0.0)

    def check_budget(self, limit_usd: float) -> tuple[bool, float, float]:
        """Return (allowed, spent, remaining).

        allowed=False means the budget is exhausted and the call should be blocked.
        """
        spent = self.month_spend_usd()
        remaining = max(0.0, limit_usd - spent)
        allowed = spent < limit_usd
        return allowed, spent, remaining


def default_limit_usd() -> float:
    """Read DARWIN_BUDGET_USD env var; default $50."""
    try:
        return float(os.environ.get("DARWIN_BUDGET_USD", "50"))
    except ValueError:
        return 50.0


__all__ = ["BudgetLedger", "default_limit_usd", "BUDGET_PATH"]
