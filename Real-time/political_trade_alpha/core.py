"""Validated official-disclosure records -> capped hypothetical buy notionals.

No broker clients, credentials, network requests, or real order functions exist here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


MIN_ORDER = Decimal("5.00")
PER_SYMBOL_CAP = Decimal("0.01")
DAILY_CAP = Decimal("0.03")
MAX_TRADE_AGE_DAYS = 45
HALF_LIFE_DAYS = 21
CENT = Decimal("0.01")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class InvalidFiling(ValueError):
    """Record is missing material verification, provenance or financial fields."""


def official_disclosure_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return False
        if host == "disclosures-clerk.house.gov":
            return True
        if host in {"efdsearch.senate.gov", "efd.senate.gov"}:
            return True
        if host == "oge.gov" or host.endswith(".oge.gov"):
            return True
        return False
    except ValueError:
        return False


def _amount(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidFiling("Invalid amount") from exc
    if not result.is_finite() or result <= 0:
        raise InvalidFiling("Amounts must be finite and positive")
    return result


@dataclass(frozen=True)
class Trade:
    politician: str
    symbol: str
    traded_on: date
    disclosed_on: date
    low: Decimal
    high: Decimal
    source_url: str
    verification_note: str

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> "Trade":
        if item.get("verified") is not True:
            raise InvalidFiling("Official filing not independently verified")
        if str(item.get("side", "")).upper() != "BUY":
            raise InvalidFiling("Only BUY transactions are supported")
        if str(item.get("asset_type", "")).upper() != "STOCK":
            raise InvalidFiling("Only individual STOCK transactions are supported")
        politician = str(item.get("politician", "")).strip()
        symbol = str(item.get("symbol", "")).strip().upper()
        url = str(item.get("source_url", "")).strip()
        note = str(item.get("verification_note", "")).strip()
        if not politician or not note:
            raise InvalidFiling("Politician and verification note required")
        if not SYMBOL_RE.fullmatch(symbol):
            raise InvalidFiling("Invalid U.S. stock symbol")
        if not official_disclosure_url(url):
            raise InvalidFiling("Official House, Senate, or OGE HTTPS source required")
        try:
            trade_date = date.fromisoformat(str(item["trade_date"]))
            disclosure_date = date.fromisoformat(str(item["disclosure_date"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidFiling("Valid ISO trade and public disclosure dates required") from exc
        if disclosure_date < trade_date:
            raise InvalidFiling("Disclosure cannot predate trade")
        try:
            low = _amount(item["amount_low"])
            high = _amount(item["amount_high"])
        except KeyError as exc:
            raise InvalidFiling("Both ends of the disclosed range are required") from exc
        if low > high:
            raise InvalidFiling("Disclosure range lower bound exceeds upper bound")
        return cls(politician, symbol, trade_date, disclosure_date, low, high, url, note)

    @property
    def event_key(self) -> str:
        canonical = [
            self.politician.casefold(),
            self.symbol,
            self.traded_on.isoformat(),
            self.disclosed_on.isoformat(),
            str(self.low.normalize()),
            str(self.high.normalize()),
            self.source_url,
        ]
        return sha256(json.dumps(canonical, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]

    @property
    def reporting_lag_days(self) -> int:
        return (self.disclosed_on - self.traded_on).days


@dataclass(frozen=True)
class Signal:
    trade: Trade
    event_key: str
    paper_notional: Decimal
    age_days: int
    reason: str


def candidate_notional(trade: Trade, equity: Decimal, as_of: date) -> Decimal:
    """Proportional buy candidate; missing/old/future data never creates exposure."""
    if not equity.is_finite() or equity <= 0:
        raise ValueError("Paper equity must be finite and positive")
    age = (as_of - trade.traded_on).days
    if age < 0 or age > MAX_TRADE_AGE_DAYS or trade.disclosed_on > as_of:
        return Decimal("0.00")
    representative = (trade.low * trade.high).sqrt()
    decay = Decimal(str(math.pow(2.0, -trade.reporting_lag_days / HALF_LIFE_DAYS)))
    raw = Decimal("0.005") * equity * representative / Decimal("10000") * decay
    return min(equity * PER_SYMBOL_CAP, raw).quantize(CENT, rounding=ROUND_DOWN)


def evaluate(
    records: Iterable[Mapping[str, Any]],
    processed_keys: set[str],
    equity: Decimal,
    as_of: date,
) -> tuple[list[Signal], set[str], list[str]]:
    """Return simulated signals, updated deduplication keys and validation errors.

    Invalid and unverified records do not change state. Verified-but-stale records
    are recorded as seen with zero notional to avoid repeated alerts.
    """
    if not equity.is_finite() or equity <= 0:
        raise ValueError("Paper equity must be finite and positive")
    valid: list[Trade] = []
    errors: list[str] = []
    for i, raw in enumerate(records):
        try:
            if not isinstance(raw, dict):
                raise InvalidFiling("Entry must be an object")
            valid.append(Trade.from_mapping(raw))
        except (InvalidFiling, TypeError, ValueError) as exc:
            errors.append(f"record {i + 1}: {exc}")
    valid.sort(key=lambda t: (t.disclosed_on, t.politician.casefold(), t.symbol, t.event_key))

    next_keys = set(processed_keys)
    signals: list[Signal] = []
    per_symbol: dict[str, Decimal] = {}
    spent_today = Decimal("0.00")
    for trade in valid:
        key = trade.event_key
        if key in next_keys:
            continue
        age = (as_of - trade.traded_on).days
        if trade.disclosed_on > as_of or age < 0:
            errors.append(f"event {key}: future trade/disclosure date")
            continue
        candidate = candidate_notional(trade, equity, as_of)
        available_symbol = max(Decimal(0), equity * PER_SYMBOL_CAP - per_symbol.get(trade.symbol, Decimal(0)))
        available_day = max(Decimal(0), equity * DAILY_CAP - spent_today)
        amount = min(candidate, available_symbol, available_day).quantize(CENT, rounding=ROUND_DOWN)
        if age > MAX_TRADE_AGE_DAYS:
            amount, reason = Decimal("0.00"), "STALE_OVER_45_DAYS"
        elif amount < MIN_ORDER:
            amount, reason = Decimal("0.00"), "BELOW_MINIMUM_OR_CAP"
        else:
            reason = "PAPER_BUY_CANDIDATE"
            per_symbol[trade.symbol] = per_symbol.get(trade.symbol, Decimal(0)) + amount
            spent_today += amount
        signals.append(Signal(trade, key, amount, age, reason))
        next_keys.add(key)
    return signals, next_keys, errors
