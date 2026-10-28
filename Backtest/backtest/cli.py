"""Reproducible end-of-day disclosure-following historical simulation."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import Config, backtest
from .sources import download_slushfund, load_prices, read_form4_csv


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMBERS = ("Nancy Pelosi", "Ro Khanna", "Tommy Tuberville")


def save_csv(path: Path, records: list[dict]) -> None:
    if not records:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1))
    parser.add_argument("--members", default=",".join(DEFAULT_MEMBERS),
                        help="Comma-separated, preselected politicians (or ALL)")
    parser.add_argument("--investor-form4", type=Path, default=ROOT / "data" / "form4_verified.csv")
    parser.add_argument("--capital", type=float, default=10_000)
    parser.add_argument("--allocation", type=float, default=0.10)
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--offline", action="store_true", help="Use saved disclosure and price cache")
    parser.add_argument("--reuse-disclosures", action="store_true",
                        help="Reuse the captured event snapshot, refresh only missing prices")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    event_cache = args.output / "disclosure_snapshot.json"
    if args.offline or args.reuse_disclosures:
        if not event_cache.exists():
            parser.error("No cached disclosure_snapshot.json to reuse")
        from .engine import Event
        snapshot = json.loads(event_cache.read_text(encoding="utf-8"))
        events = [Event(**{**e, "transaction_date": date.fromisoformat(e["transaction_date"]),
                           "filing_date": date.fromisoformat(e["filing_date"])})
                  for e in snapshot["events"]]
        provenance = snapshot["provenance"]
    else:
        events, provenance = download_slushfund(2021, args.end.year)
        if args.investor_form4.exists():
            extra, rejected = read_form4_csv(args.investor_form4)
            events.extend(extra)
            provenance["form4_rejected"] = rejected
        event_cache.write_text(json.dumps({
            "retrieved_et": datetime.now(ZoneInfo("America/New_York")).isoformat(),
            "events": [{**asdict(e), "transaction_date": e.transaction_date.isoformat(),
                        "filing_date": e.filing_date.isoformat()} for e in events],
            "provenance": provenance}, indent=2), encoding="utf-8")

    members = {m.strip().casefold() for m in args.members.split(",") if m.strip()}
    if members != {"all"}:
        events = [e for e in events if e.source == "SEC_FORM4" or e.filer.casefold() in members]
    events = [e for e in events if e.filing_date <= args.end and e.transaction_date.year >= 2021]
    if not events:
        raise RuntimeError("No disclosed historical purchase events for the chosen universe")
    tickers = {e.symbol for e in events if e.filing_date >= args.start}
    print(f"Filings loaded: {len(events)}; symbols to price: {len(tickers)}", flush=True)
    prices, failures = load_prices(tickers, args.start, args.end, ROOT / "cache" / "prices",
                                   offline=args.offline)
    cfg = Config(capital=args.capital, allocation=args.allocation,
                 max_positions=args.max_positions, slippage_bps=args.slippage_bps)
    result = backtest(events, prices, args.start, args.end, cfg)
    result["sources"] = provenance
    result["members"] = sorted(members)
    result["price_download_failures"] = failures
    result["pricing_caveat"] = ("Yahoo auto-adjusted OHLC approximates split/dividend-adjusted total returns. "
                               "Intraday limit-touch fills are assumed, not guaranteed. "
                               "Historical delisted/rebranded symbols can be missing; if so, "
                               "survivorship bias is possible.")
    report = {k: v for k, v in result.items() if k not in ("trades", "equity_curve")}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    save_csv(args.output / "trades.csv", result["trades"])
    save_csv(args.output / "equity_curve.csv", result["equity_curve"])
    metric = result["metrics"]
    lines = ["# Political Trade Alpha — disclosure-date backtest", "",
             f"Period: {result['start_session']} — {result['end_session']}",
             f"Universe: {args.members} (plus verified Form 4 rows, if provided)",
             f"Events: {len(events)}; entered: {result['entered_positions']}; closed: {result['closed_trades']}",
             "", "| Metric | Strategy | SPY | QQQ |", "|---|---:|---:|---:|" ]
    for key in ("start_value", "end_value", "total_return_pct", "cagr_pct",
                "max_drawdown_pct", "sharpe_rf_zero"):
        lines.append("| " + key + " | " + " | ".join(str(metric[s][key]) for s in ("portfolio", "SPY", "QQQ")) + " |")
    lines.extend(["", "## Skipped / data quality", "",
                  *[f"- {k}: {v}" for k, v in result["diagnostics"].items()],
                  f"- Price download failures: {len(failures)} ({', '.join(failures[:15])})",
                  "- House public filing dates are a proxy for original public availability; "
                  "no intra-day timestamp or retroactive correction history.",
                  "- No transaction from 2021–22 is bought before 2023; no future filing is anticipated.",
                  "- Prices adjusted for splits/dividends; optimistic touched-limit fill assumption.",
                  "- SPY/QQQ are total-return ETF proxies, not exact cash indices.",
                  "- Slippage applied to strategy entries/exits; zero financing/taxes, "
                  "no dividend cash reconciliation or delisting recovery model.",
                  "- SlushFund data: https://slushfund.net/data/trades (original PDFs linked by event).",
                  "- For education/research only, no live trading.", ""])
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
