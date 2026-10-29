"""Date-aware disclosure ingestion and adjusted daily market price loading.

Public CSV aggregators are *not* independently verified original PDFs. Every
accepted event retains an official filing URL for source review.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .engine import Bar, Event


SLUSHFUND = "https://slushfund.net/data/trades/export"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def official_url(value: str) -> bool:
    try:
        url = urlparse(value)
        host = (url.hostname or "").lower()
        return (url.scheme == "https" and not url.username and not url.password
                and host in {"disclosures-clerk.house.gov", "efdsearch.senate.gov",
                             "efd.senate.gov", "www.sec.gov", "sec.gov"})
    except ValueError:
        return False


def record_key(filer: str, ticker: str, traded: date, filed: date, source: str,
               filing_url: str, owner: str = "") -> str:
    raw = "|".join((filer.casefold(), ticker, traded.isoformat(), filed.isoformat(),
                    source, filing_url, owner.casefold()))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_slushfund(rows: list[dict[str, str]]) -> tuple[list[Event], dict[str, int]]:
    excluded = {"not_stock_buy": 0, "bad_dates": 0, "bad_ticker": 0,
                "bad_official_url": 0, "date_anomaly": 0, "duplicates": 0}
    events: list[Event] = []
    seen: set[str] = set()
    for row in rows:
        if (row.get("Transaction code", "").upper() != "BUY"
                or row.get("Instrument", "").casefold() != "stock"
                or row.get("Asset type (as filed)", "").casefold() != "stock"):
            excluded["not_stock_buy"] += 1
            continue
        ticker = row.get("Ticker", "").strip().upper()
        if not SYMBOL.fullmatch(ticker):
            excluded["bad_ticker"] += 1
            continue
        url = row.get("Filing URL (first report)", "").strip()
        if not official_url(url):
            excluded["bad_official_url"] += 1
            continue
        try:
            traded = date.fromisoformat(row["Trade date"].strip())
            filed = date.fromisoformat(row["First report filed"].strip())
        except (ValueError, KeyError):
            excluded["bad_dates"] += 1
            continue
        if traded > filed or row.get("Date flag", "").startswith("impossible"):
            excluded["date_anomaly"] += 1
            continue
        filer = row.get("Member", "").strip()
        if not filer:
            excluded["bad_dates"] += 1
            continue
        key = record_key(filer, ticker, traded, filed, "HOUSE_SENATE", url,
                         row.get("Owner", ""))
        if key in seen:
            excluded["duplicates"] += 1
            continue
        seen.add(key)
        events.append(Event(key, filer, ticker, traded, filed, url))
    return events, excluded


def download_slushfund(year_from: int, year_to: int, timeout: int = 50):
    """One year per request avoids the site's 10,000-row export truncation."""
    all_rows = []
    counts = {}
    for year in range(year_from, year_to + 1):
        uri = SLUSHFUND + "?" + urlencode({"from": f"{year}-01-01",
                                           "to": f"{year}-12-31"})
        req = Request(uri, headers={"User-Agent": "PoliticalTradeAlphaResearch/0.2"})
        with urlopen(req, timeout=timeout) as response:
            rows = list(csv.DictReader(StringIO(response.read().decode("utf-8-sig"))))
        if len(rows) >= 10000:
            raise ValueError(f"Truncated {year} year export (10,000-row ceiling)")
        counts[str(year)] = len(rows)
        all_rows.extend(rows)
    events, excluded = normalize_slushfund(all_rows)
    return events, {"provider": "SlushFund (House 2021+, Senate 2024+)",
                    "raw_rows_by_trade_year": counts, "rejected": excluded,
                    "coverage_warning": "Historical CSV may be retroactively corrected; first-report filed DATE is a proxy for public availability."}


def read_form4_csv(path: Path) -> tuple[list[Event], int]:
    """Optional manually verified SEC Form 4 purchases, not 13F holdings."""
    events = []
    rejected = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                if (row.get("verified", "").strip().lower() != "true"
                        or row.get("transaction_code", "").upper() != "P"
                        or row.get("security_type", "").upper() != "STOCK"):
                    raise ValueError("Not verified Form 4 stock purchase")
                ticker = row["ticker"].strip().upper()
                filer = row["filer"].strip()
                url = row["filing_url"].strip()
                traded = date.fromisoformat(row["transaction_date"])
                filed = date.fromisoformat(row["filing_date"])
                if (not SYMBOL.fullmatch(ticker) or not filer or traded > filed
                        or urlparse(url).hostname not in ("www.sec.gov", "sec.gov")
                        or not official_url(url)):
                    raise ValueError("Invalid Form 4 fields")
                events.append(Event(record_key(filer, ticker, traded, filed, "SEC_FORM4", url),
                                    filer, ticker, traded, filed, url, "SEC_FORM4"))
            except (KeyError, ValueError):
                rejected += 1
    return events, rejected


def _read_price_csv(path: Path) -> dict[date, Bar]:
    history = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                history[date.fromisoformat(row["date"])] = Bar(
                    *[float(row[k]) for k in ("open", "high", "low", "close")])
            except (KeyError, ValueError):
                continue
    return history


def load_prices(tickers: set[str], start: date, end: date, cache: Path,
                offline: bool = False) -> tuple[dict[str, dict[date, Bar]], list[str]]:
    """yfinance OHLC with auto_adjust=True (split/dividend total-return proxy)."""
    cache.mkdir(parents=True, exist_ok=True)
    history = {}
    failures = []
    missing = []
    for ticker in sorted(tickers | {"SPY", "QQQ"}):
        path = cache / (ticker.replace(".", "_") + ".csv")
        if path.exists():
            known = _read_price_csv(path)
            # A ticker may IPO after start or delist before end. Do not discard
            # valid historical bars just because it did not trade for the full period.
            if known and (offline or max(known) >= end - timedelta(days=7)
                          or ticker in {"LSXMA", "LSXMK", "SSBK", "WBA"}):
                history[ticker] = known
                continue
        missing.append(ticker)
    if missing and not offline:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("Install Backtest/requirements.txt for price downloading") from exc
        for ticker in missing:
            path = cache / (ticker.replace(".", "_") + ".csv")
            yahoo_symbol = ticker.replace(".", "-")
            try:
                frame = yf.Ticker(yahoo_symbol).history(
                    start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                    auto_adjust=True, actions=False)
                if frame.empty:
                    raise ValueError("No market bars")
                bars = {}
                for index, row in frame.iterrows():
                    try:
                        day = index.date()
                        bars[day] = Bar(float(row["Open"]), float(row["High"]),
                                        float(row["Low"]), float(row["Close"]))
                    except (TypeError, ValueError):
                        continue
                if not bars:
                    raise ValueError("No valid market bars")
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["date", "open", "high", "low", "close"])
                    for d, b in sorted(bars.items()):
                        writer.writerow([d.isoformat(), b.open, b.high, b.low, b.close])
                history[ticker] = bars
            except Exception as exc:
                failures.append(f"{ticker}: {type(exc).__name__}")
    else:
        failures.extend(f"{ticker}: cache missing" for ticker in missing)
    if "SPY" not in history or "QQQ" not in history:
        raise RuntimeError("Both benchmark price series are required; unavailable: " + ", ".join(failures))
    return history, failures
