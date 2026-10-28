"""Next-session entries, daily-high take profits, and calendar-year exits.

Daily bars cannot guarantee that a touched limit order fills. This simulation
assumes it does, with configurable slippage. Results remain hypothetical.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from math import floor, sqrt
from statistics import mean, pstdev


@dataclass(frozen=True)
class Event:
    key: str
    filer: str
    symbol: str
    transaction_date: date
    filing_date: date
    filing_url: str
    source: str = "HOUSE_SENATE"


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self):
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("Inconsistent OHLC prices")


@dataclass
class Position:
    symbol: str
    shares: int
    entry_date: date
    entry_price: float
    cost: float
    filer: str
    filing_url: str
    event_key: str


@dataclass(frozen=True)
class Config:
    capital: float = 10_000.0
    allocation: float = 0.10
    max_positions: int = 10
    take_profit: float = 0.10
    slippage_bps: float = 5.0
    commission: float = 0.0

    def __post_init__(self):
        if not (self.capital > 0 and 0 < self.allocation <= 1 and self.max_positions > 0):
            raise ValueError("Invalid capital, allocation, or position limit")
        if not (0 < self.take_profit < 10 and 0 <= self.slippage_bps < 1000 and self.commission >= 0):
            raise ValueError("Invalid target or execution cost")


def anniversary(day: date) -> date:
    """February 29 matures on February 28 the following year."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def metrics(values: list[float], trading_days: int) -> dict[str, float]:
    initial, final = values[0], values[-1]
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    peak, mdd = initial, 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1)
    vol = pstdev(returns) if len(returns) >= 2 else 0.0
    years = max(trading_days / 252, 1 / 252)
    return {
        "start_value": round(initial, 2), "end_value": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 3),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 3),
        "max_drawdown_pct": round(mdd * 100, 3),
        "sharpe_rf_zero": round(mean(returns) / vol * sqrt(252), 3) if vol else 0.0,
    }


def backtest(events: list[Event], prices: dict[str, dict[date, Bar]],
             start: date, end: date, cfg: Config | None = None) -> dict:
    cfg = cfg or Config()
    if end <= start:
        raise ValueError("End date must follow start date")
    if "SPY" not in prices or "QQQ" not in prices:
        raise ValueError("SPY and QQQ adjusted daily OHLC are required")
    sessions = sorted(d for d in prices["SPY"] if start <= d <= end and d in prices["QQQ"])
    if len(sessions) < 2:
        raise ValueError("Not enough common trading sessions")

    # Filed date is observable; transaction date alone must NEVER trigger entry.
    # With date-only filings, assume public at end of filing date, enter strictly later.
    agenda: dict[date, list[Event]] = {}
    seen: set[str] = set()
    diagnostic = {"prestart_filings": 0, "postend_filings": 0,
                  "duplicate_events": 0, "no_next_session": 0,
                  "no_entry_price": 0, "capacity": 0,
                  "duplicate_held_symbol": 0, "unpriceable_positions": 0,
                  "unaffordable": 0}
    for event in sorted(events, key=lambda e: (e.filing_date, e.filer, e.symbol, e.key)):
        if event.key in seen:
            diagnostic["duplicate_events"] += 1
            continue
        seen.add(event.key)
        if event.filing_date < start:
            diagnostic["prestart_filings"] += 1
            continue
        if event.filing_date > end:
            diagnostic["postend_filings"] += 1
            continue
        i = bisect_right(sessions, event.filing_date)
        if i == len(sessions):
            diagnostic["no_next_session"] += 1
            continue
        agenda.setdefault(sessions[i], []).append(event)

    cash = cfg.capital
    positions: dict[str, Position] = {}
    last_marks: dict[str, float] = {}
    trades: list[dict] = []
    curve: list[dict] = []
    slip = cfg.slippage_bps / 10_000

    def sell(symbol: str, today: date, raw_price: float, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(symbol)
        fill = raw_price * (1 - slip)
        proceeds = pos.shares * fill - cfg.commission
        cash += proceeds
        trades.append({
            "symbol": symbol, "filer": pos.filer, "entry_date": pos.entry_date.isoformat(),
            "exit_date": today.isoformat(), "entry_price": round(pos.entry_price, 5),
            "exit_price": round(fill, 5), "shares": pos.shares,
            "pnl_usd": round(proceeds - pos.cost, 2),
            "return_pct": round((proceeds / pos.cost - 1) * 100, 3),
            "holding_days": (today - pos.entry_date).days, "reason": reason,
            "event_key": pos.event_key, "filing_url": pos.filing_url,
        })

    for session_index, day in enumerate(sessions):
        for symbol, pos in list(positions.items()):
            current = prices.get(symbol, {}).get(day)
            expiry = anniversary(pos.entry_date)
            next_day = sessions[session_index + 1] if session_index + 1 < len(sessions) else None
            # If anniversary falls on a closed exchange day, sell the last
            # session before it rather than holding beyond one calendar year.
            due = day >= expiry or (next_day is not None and day < expiry < next_day)
            if current and due:
                sell(symbol, day, current.open, "MAX_ONE_YEAR")

        opening_equity = cash + sum(
            p.shares * (prices.get(s, {}).get(day).open
                        if prices.get(s, {}).get(day) else last_marks.get(s, p.entry_price))
            for s, p in positions.items()
        )
        for event in agenda.get(day, []):
            symbol = event.symbol
            if symbol in positions:
                diagnostic["duplicate_held_symbol"] += 1
                continue
            if len(positions) >= cfg.max_positions:
                diagnostic["capacity"] += 1
                continue
            bar = prices.get(symbol, {}).get(day)
            if bar is None:
                diagnostic["no_entry_price"] += 1
                continue
            price = bar.open * (1 + slip)
            budget = min(opening_equity * cfg.allocation, cash - cfg.commission)
            shares = max(0, floor(budget / price))
            if not shares:
                diagnostic["unaffordable"] += 1
                continue
            cost = shares * price + cfg.commission
            cash -= cost
            positions[symbol] = Position(symbol, shares, day, price, cost,
                                         event.filer, event.filing_url, event.key)

        for symbol, pos in list(positions.items()):
            bar = prices.get(symbol, {}).get(day)
            if not bar:
                continue
            target = pos.entry_price * (1 + cfg.take_profit)
            if bar.open >= target:
                sell(symbol, day, bar.open, "TAKE_PROFIT_GAP")
            elif bar.high >= target:
                sell(symbol, day, target, "TAKE_PROFIT_LIMIT")

        holding_value = 0.0
        for symbol, pos in positions.items():
            bar = prices.get(symbol, {}).get(day)
            if bar:
                last_marks[symbol] = bar.close
            else:
                diagnostic["unpriceable_positions"] += 1
            holding_value += pos.shares * last_marks.get(symbol, pos.entry_price)
        nav = cash + holding_value
        first = sessions[0]
        curve.append({"date": day.isoformat(), "portfolio": round(nav, 4),
                      "SPY": round(cfg.capital * prices["SPY"][day].close /
                                   prices["SPY"][first].open, 4),
                      "QQQ": round(cfg.capital * prices["QQQ"][day].close /
                                   prices["QQQ"][first].open, 4),
                      "cash": round(cash, 4), "open_positions": len(positions)})

    if not trades and not positions:
        raise ValueError("No executed positions: insufficient events, quotes, or capacity")
    measures = {name: metrics([cfg.capital] + [d[name] for d in curve], len(sessions))
                for name in ("portfolio", "SPY", "QQQ")}
    return {"start_session": sessions[0].isoformat(), "end_session": sessions[-1].isoformat(),
            "config": vars(cfg), "metrics": measures, "diagnostics": diagnostic,
            "submitted_events": len(events), "entered_positions": len(trades) + len(positions),
            "closed_trades": len(trades),
            "win_rate_pct": round(100 * sum(t["pnl_usd"] > 0 for t in trades) / len(trades), 2)
                            if trades else None,
            "open_positions": [{"symbol": p.symbol, "shares": p.shares,
                                "entry_date": p.entry_date.isoformat(), "event_key": p.event_key}
                               for p in positions.values()],
            "trades": trades, "equity_curve": curve}
