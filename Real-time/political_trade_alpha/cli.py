"""Scheduled news context + official-disclosure paper-signal report.

Only reads public RSS and local reviewed trade records. Does not place orders.
"""
from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
import xml.etree.ElementTree as XML
from zoneinfo import ZoneInfo

from .core import evaluate


ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
SLOTS = {"09:00", "11:59", "14:00"}
RSS_URLS = {
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "CNBC Top News": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}
STATE_PATH = ROOT / "data" / "state.json"
TRADES_PATH = ROOT / "data" / "verified_trades.json"


def scheduled_slot_is_due(cron: str, utc_day: date) -> bool:
    """Check a UTC cron slot against US DST; avoid two runs per local slot."""
    try:
        minute, hour, _, _, _ = cron.split()
        if not (0 <= int(minute) < 60 and 0 <= int(hour) < 24):
            return False
        instant = datetime(utc_day.year, utc_day.month, utc_day.day,
                           int(hour), int(minute), tzinfo=timezone.utc)
        return instant.astimezone(NY).strftime("%H:%M") in SLOTS
    except (ValueError, TypeError):
        return False


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fetch_headlines(max_per_source: int = 6) -> tuple[list[dict[str, str]], list[str]]:
    news: list[dict[str, str]] = []
    problems: list[str] = []
    for label, url in RSS_URLS.items():
        try:
            request = Request(url, headers={"User-Agent": "PoliticalTradeAlphaPaperResearch/0.1"})
            with urlopen(request, timeout=15) as response:
                body = response.read(2_000_000)
            root = XML.fromstring(body)
            for item in root.findall(".//channel/item")[:max_per_source]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                published = (item.findtext("pubDate") or "").strip()
                try:
                    dt = parsedate_to_datetime(published)
                    published = dt.astimezone(timezone.utc).isoformat()
                except (TypeError, ValueError):
                    published = "UNKNOWN"
                if title and link.startswith("https://"):
                    news.append({"source": label, "title": title, "link": link, "published": published})
        except Exception as exc:
            problems.append(f"{label}: {type(exc).__name__} retrieving RSS")
    return news, problems


def plain(value: object) -> str:
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")


def create_report(now: datetime, equity: Decimal, signals, errors, news, news_errors,
                  input_count: int) -> str:
    lines = [
        "# Political Trade Alpha — paper-only monitoring",
        "",
        f"- Run: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (America/New_York)",
        f"- Simulated equity: USD {equity:,.2f}",
        f"- Input disclosures: {input_count}; NEW validated events: {len(signals)}",
        "- Actual broker orders: **0 — not implemented**",
        "- The recorded disclosure date is not necessarily the public release timestamp.",
        "",
        "## New verified-file BUY events (simulated amounts only)",
        "",
        "| Official filer | Symbol | Transaction | Disclosure | Reported amount range | Lag | Age | Paper USD | Status | Original filing |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not signals:
        lines.append("| No new verified eligible records | — | — | — | — | — | — | — | — | — |")
    for signal in signals:
        t = signal.trade
        link = t.source_url
        lines.append(
            f"| {plain(t.politician)} | {plain(t.symbol)} | {t.traded_on.isoformat()} | "
            f"{t.disclosed_on.isoformat()} | USD {t.low:,.0f}–{t.high:,.0f} | "
            f"{t.reporting_lag_days}d | {signal.age_days}d | "
            f"USD {signal.paper_notional:,.2f} | {signal.reason} | [source]({link}) |"
        )
    lines += ["", "## News headlines (context, not trade signals)", ""]
    if not news:
        lines.append("No headlines retrieved; this is NOT proof that no important news exists.")
    for item in news:
        lines.append(f"- {plain(item['source'])} · {plain(item['published'])}: "
                     f"[{plain(item['title'])}]({item['link']})")
    lines += ["", "## Data quality and limitations", ""]
    if input_count == 0:
        lines.append("- **No reviewed political filing records configured.** The system cannot claim that no politicians traded.")
    else:
        lines.append("- Records are marked independently verified in the input file. Recheck original PDFs; URL and flag do not prove authenticity.")
    if errors:
        lines.append(f"- {len(errors)} invalid/unverified record(s) skipped:")
        lines.extend(f"  - {plain(problem)}" for problem in errors[:25])
    else:
        lines.append("- All supplied records passed structural checks.")
    if news_errors:
        lines.append("- RSS access problems: " + "; ".join(news_errors))
    else:
        lines.append("- RSS sources returned without transport errors; coverage is not exhaustive.")
    lines += [
        "- Trades disclosed up to 45 days after the event may have no predictive edge.",
        "- No real-time quotation, spread, slippage, market-calendar, or broker integration is provided.",
        "- This is a research hypothesis and requires backtesting on public-availability timestamps.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    utc_now = datetime.now(timezone.utc)
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        cron = os.environ.get("SCHEDULED_CRON", "")
        if not scheduled_slot_is_due(cron, utc_now.date()):
            print(f"Skipping redundant DST UTC slot: {cron}")
            return
    now = utc_now.astimezone(NY)
    equity = Decimal(os.environ.get("PAPER_EQUITY_USD", "10000"))
    if not equity.is_finite() or equity <= 0:
        raise ValueError("PAPER_EQUITY_USD must be positive")
    records = read_json(TRADES_PATH, [])
    if not isinstance(records, list):
        raise ValueError("data/verified_trades.json must contain a JSON array")
    prior = read_json(STATE_PATH, {"processed_event_keys": []})
    if not isinstance(prior, dict) or not isinstance(prior.get("processed_event_keys"), list):
        raise ValueError("Invalid data/state.json")
    seen = set(str(key) for key in prior["processed_event_keys"])
    signals, next_keys, invalid_records = evaluate(records, seen, equity, now.date())
    headlines, headline_errors = fetch_headlines()
    report = create_report(now, equity, signals, invalid_records, headlines,
                           headline_errors, len(records))
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / now.strftime("%Y-%m-%d_%H%M_ET.md")
    report_file.write_text(report, encoding="utf-8")
    if next_keys != seen:
        STATE_PATH.write_text(
            json.dumps({"processed_event_keys": sorted(next_keys)}, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Report: {report_file.relative_to(ROOT)}")
    print(f"New reviewed transactions: {len(signals)}; paper order candidates: "
          f"{sum(signal.paper_notional > 0 for signal in signals)}")
    print("LIVE ORDERS: 0 (not implemented)")


if __name__ == "__main__":
    main()
