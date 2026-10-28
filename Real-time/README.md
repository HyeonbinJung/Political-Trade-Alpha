# Political Trade Alpha

A disclosure-aware, **paper-only** political trading research pipeline for U.S. equities.

## Status

- Market-news monitoring: public RSS headlines, with source and publication time.
- Political trading input: manually verified official U.S. House/Senate/OGE filing records in data/verified_trades.json.
- Signal generation: proportional notional size, disclosure-lag decay, duplicate protection, per-symbol and daily caps.
- Execution: **NO LIVE ORDERS.** This repository has no broker API or order-submission code.
- Orchestration: GitHub Actions at 09:00, 11:59 and 14:00 America/New_York, with DST gating. GitHub scheduled workflow runs can be delayed.
- ChatGPT news/disclosure search tasks may supplement the input file after comparing each entry with the original official filing. The repository does not autonomously discover or authenticate new PTRs.

## How it works

1. Check major business/market headlines through RSS. Headlines are *context*, never a standalone trading instruction.
2. Use an independently checked official disclosure to append a BUY transaction to data/verified_trades.json. Never infer trades from headlines or social posts.
3. Run the paper monitor. It validates records, computes signal notional, and writes a Markdown report.
4. Remember processed transaction fingerprints in data/state.json so the same disclosed transaction is not traded twice.
5. Save paper reports under reports/. Neither the workflow nor the Python code places any orders.

Official disclosure references:
- U.S. House: https://disclosures-clerk.house.gov/FinancialDisclosure
- U.S. Senate: https://efdsearch.senate.gov/search/home/
- Executive branch (OGE): https://www.oge.gov/web/oge.nsf/publicresources_disclosure-faq

**Disclosure delay matters.** Reportable transactions may appear up to 45 days after the actual trade. An immediate alert after publication is not an immediate copy of the politician's trade.

## Paper sizing

Let E be portfolio equity, L and U the *reported range* of the original purchase, d the number of days from transaction to filing:

    A = sqrt(L * U)
    base = 0.005 * E * (A / 10000) * 2 ** (-d / 21)
    candidate_notional = min(0.01 * E, base)

Then cap all signals in one stock at 1% of E and all signals in one day at 3% of E. Ignore candidates under $5 and trades more than 45 calendar days old, even if newly discovered. Default E=$10,000 **simulated**. No model performance or alpha has been established. This is a hypothesis for backtesting, not investment advice.

### Record schema

Put an array in data/verified_trades.json. Example **illustration only; do not insert an invented filing**:

    {
      "politician": "Official full name",
      "symbol": "AAPL",
      "asset_type": "STOCK",
      "side": "BUY",
      "trade_date": "2026-10-01",
      "disclosure_date": "2026-10-20",
      "amount_low": 1001,
      "amount_high": 15000,
      "source_url": "https://disclosures-clerk.house.gov/...",
      "verified": true,
      "verification_note": "Personally checked the filing PDF and ticker"
    }

A URL matching an official domain and a verified flag do **not** cryptographically prove authenticity. Original PDFs still require independent checking. The pipeline rejects other instruments and unverified records. There are no included real trade claims.

## Run

Requires Python 3.11+ and only the standard library.

    python -m unittest discover -s tests -v
    python -m political_trade_alpha.cli

Optionally set PAPER_EQUITY_USD to change simulated equity. The default monitor saves data/state.json and a timestamped report under reports/. News RSS unavailability is disclosed and does not become a false assertion that no news exists.

GitHub Actions runs on the schedule and on updates to data/verified_trades.json; it commits paper reports and state changes using the repository's GitHub Actions token. Give workflow permissions to read/write contents in repository settings if necessary. Scheduled workflows can be delayed, so this is **not a low-latency execution system**.

## Before any real-money trading

A separate broker integration, verified quote/liquidity feed, exchange calendar, portfolio reconciliation, position and cash checks, slippage testing, backtests with *public availability timestamps*, human-approved limits, kill switch, audit trail, and explicit account/budget authorization are required. Do not put credentials in the repository. Paper mode is deliberately not switchable to live mode with a flag.

## Sources and limitations

The House/Senate/OGE filing systems do not guarantee convenient free APIs or instant disclosures. The ingestion file is a human/agent-reviewed interface, not an automatic official-PTR scraper. Reported amounts are often broad bands. Public news may be revised or time-shifted. Backtests must use the date the disclosure became public, not the earlier transaction date, to avoid look-ahead bias.

License: MIT; see LICENSE.
