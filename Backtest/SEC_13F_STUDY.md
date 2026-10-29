# SEC Form 4, 13F and 4-way take-profit study

Run from the repository root with Python 3.12:

    python -m pip install -r Backtest/requirements.txt
    cd Backtest
    python -m unittest discover -s tests -v
    SEC_USER_AGENT="PoliticalTradeAlpha research contact@extriver.com" python -m backtest.study --start 2023-01-01 --end 2026-10-27

For offline replay (only after successfully caching filings and prices):

    python -m backtest.study --offline

For reusing the historical filing snapshot while refreshing price data:

    python -m backtest.study --reuse-disclosures

Outputs live in Backtest/output/study/ (disclosures.json, summary.json,
scenarios.csv, report.md). Historical price data are cached in
Backtest/cache/prices; SEC XML and OpenFIGI mapping cache under
Backtest/cache. Data downloads are external and may be throttled. A manual
GitHub Actions workflow can run the historical collection and upload artifacts.

Cohorts: fixed 3 Congress filers (Pelosi, Tuberville, Khanna); Berkshire
Hathaway beneficial-owner Form 4 open-market P/A acquired common stock;
Berkshire Hathaway and Pershing Square 13F NEW CUSIPs versus the preceding
valid quarter; and the combined same-capital portfolio.

Each cohort has four exit rules: +10%, +15%, +20% or no take-profit with
maximum 1 calendar year. Beginning capital is $10,000 per portfolio;
each entry is capped at 10% equity with maximum 10 simultaneously held
names, same ticker cannot be held twice, 5bps modeled per trading side.
Compare with buy-and-hold SPY and QQQ. Market buy occurs at the next
regular session open STRICTLY LATER than the filing's public date (NOT
the politician transaction date, Form 4 execution date, or 13F report date).
No trades can occur in 2022. Date-only availability is conservatively
end-of-day, so intraday disclosure timestamps are not exploited.

Data caveats:
- SEC submissions and OpenFIGI security resolution are not a
  point-in-time immutable historical feed and can create revision bias.
- 13F cannot identify actual buy date; first observed report is baseline.
  CUSIPs not confidently mapped to a unique US equity ticker are excluded.
- Yahoo adjusted OHLC is a total-return proxy, but a daily intraday
  high touching a target does NOT guarantee a limit-order fill.
- The engine rejects a held ticker after five sessions without prices,
  instead of keeping a stale NAV indefinitely. Unpriced entry signals
  are also counted. Results with missing coverage cannot establish alpha.
- Cash deals can be settled using verified cash amount. WBA acquired on
  2025-08-28 for $11.45 cash plus an unvalued contingent DAP right, excluded
  rather than assumed to be worth $3. SSBK -> FBK at 0.8 share and
  LSXMA/LSXMK -> SIRI at 0.8375 share. Fractional cash-in-lieu approximated.
- Ticker reuse and post-merger share-class identification need further
  review; an unresolved 13F or delisted security should not be treated
  as a winner. No broker execution or investment advice.

Corporate actions primary references:
- https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-463
- https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-334
- https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2024-421

Prior in-conversation figures (not verified in this upload): The earlier
session calculated a 16-strategy provisional table, but its historical
source snapshots and price cache are not available to the current tool
runtime. DO NOT cite those figures as freshly rerun or as audited results.
The output from backtest.study, with failed scenarios and exclusions
reported, is the authoritative reproducible artifact.
