# Backtest — disclosure-following portfolio

## Hypothesis & clock

- Download U.S. House/Senate disclosed purchase records **with trade dates from 2021 onward**; House and Senate historical coverage differ.
- Trading starts **2023-01-01**, meaning the first U.S. trading session is **2023-01-03**. A filing dated 2023-01-02 may be bought 2023-01-03, but never before it was published. A filing dated 2023-01-03 may be bought **no earlier than 2023-01-04**.
- **Only the first reported PUBLIC filing date** determines order eligibility. The politician's original transaction date is NEVER used as a signal time. Date-only filings are assumed publicly available only after that filing day, so even intraday/after-hours releases are scheduled at the *following* trading session's open.
- Buy at next session's **opening price**, with 5 basis points of simulated slippage (default) and integer shares; hold only one position per ticker at once. Equal 10%-NAV target allocation per eligible filing, maximum 10 positions, no leverage. Contending events processed deterministically by filing date/filer/ticker; unused cash earns 0.
- Sell whenever an adjusted OHLC intraday **high reaches entry fill × 1.10**. A gap at the open over the threshold sells at the opening price; otherwise a limit sell at the threshold is **assumed filled** (optimistic, because daily highs do not prove limit-order fills), with 5 bps exit slippage.
- If no target hit, sell at the opening on the anniversary trading session, or the **last trading session before the one-calendar-year anniversary** when the anniversary is a holiday/weekend. Positions not yet expired by test end are marked to market; the report does not presume a sale. No stop-loss or signal-following sells.
- Compare identical initial capital with buy-and-hold **SPY** (S&P 500 ETF) and **QQQ** (Nasdaq-100 ETF), both marked to market using dividend/split-adjusted OHLC. Their ETF returns are *proxies*, not exact published index levels. Benchmark execution costs are not applied.

## Data lineage

- Default congressional dataset: [SlushFund disclosures](https://slushfund.net/data/trades); yearly CSV exports preserve first-report filing date and link to original House/Senate government PTR PDF. **House coverage 2021+, Senate coverage 2024+**; not a complete 2021 Senate panel. Source files can be corrected after initial publication. No official historical publication timestamp or point-in-time vendor snapshots are guaranteed.
- A named cohort fixed *before* the test period is the default: **Nancy Pelosi, Ro Khanna, Tommy Tuberville**. Change with `--members 'Name A,Name B'`; `--members ALL` uses every eligible congressional filing. This universe choice affects returns and must be stated alongside metrics; it is **not** learned from the best-performing 2023–26 traders.
- Optional investor source: edit [`data/form4_verified.csv`](data/form4_verified.csv) with genuine SEC Form 4 **open-market** stock purchases (`transaction_code=P`, `security_type=STOCK`, `verified=true`) and original SEC filing URL. Manual evidence review is required; the mere presence of a URL and Boolean does not authenticate a filing.
- Form 13F is a quarterly **holdings snapshot**, not a record of each transaction's purchase date. It is intentionally not mixed into this next-open event strategy. Do not infer purchase dates from report-period ends.
- Prices use [yfinance](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html) dividend/split-adjusted daily OHLC. These are a total-return approximation, not historically tradeable raw fills. Tickers may have been delisted, renamed, or otherwise missed; missing quotes are counted, not filled with invented values.

## Execute

Python 3.11+:

```bash
cd Backtest
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m backtest.cli --start 2023-01-01 --end 2026-10-27
```

Result files (excluded from Git):

| File | Contents |
|---|---|
| `output/disclosure_snapshot.json` | Downloaded historical events and ingestion summary for offline reruns |
| `cache/prices/*.csv` | Adjusted OHLC used for simulation |
| `output/report.md` | Portfolio vs. SPY and QQQ, caveats and rejected-trade reasons |
| `output/summary.json` | Reusable machine-readable statistics |
| `output/equity_curve.csv` | Daily equity curve, cash, and open-position count |
| `output/trades.csv` | Executed closed trades, P/L, exit reasons, source filing |

To replay the *same* cached events/prices rather than a revised provider dataset:

```bash
python -m backtest.cli --start 2023-01-01 --end 2026-10-27 --offline
```

To retry failed price downloads while holding the disclosure snapshot fixed:

```bash
python -m backtest.cli --start 2023-01-01 --end 2026-10-27 --reuse-disclosures
```

For all Congressional filers (more price downloads, stronger missing-data risks):

```bash
python -m backtest.cli --members ALL --start 2023-01-01 --end 2026-10-27
```

If no valid events/trades or no SPY/QQQ prices exist, the run **fails**, rather than producing a false 0%-return victory. The output logs skipped entry prices and missing marks; **any missing price should be treated as potential survivorship bias**, not a validated alpha estimate. This is a fixed-rule, point-in-time-approximated research backtest, not investment advice.
