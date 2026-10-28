# Political Trade Alpha

Disclosure-aware research for hypothetical U.S. stock trades. **No live brokerage orders.**

| Component | Purpose | Entry point |
|---|---|---|
| [Real-time](Real-time/README.md) | Existing paper-only official-filing monitor and news RSS reports | `cd Real-time && python -m political_trade_alpha.cli` |
| [Backtest](Backtest/README.md) | Historical filings **as known on filing dates**, next-session buy, +10% sell or one-year expiry, versus SPY and QQQ | `cd Backtest && python -m backtest.cli` |

Existing `Real-time/data`, `Real-time/reports`, `Real-time/tests`, and Python sources were moved without changing the core strategy. The scheduled workflow remains in the root `.github/workflows` directory, as GitHub requires. Repository-level license and `.gitignore` also remain at root.

Backtests currently use Congress (House coverage from 2021, Senate from 2024). A verified SEC Form 4 **open-market buy** can be added separately; 13F quarterly holdings are *not* treated as dated transactions. See limitations and reproducible run instructions in [Backtest](Backtest/README.md).

## Tests

```bash
(cd Real-time && python -m unittest discover -s tests -v)
(cd Backtest && python -m unittest discover -s tests -v)
```

Research only. Public filings are delayed, and historical backtest performance does not demonstrate a profitable or executable trading system.
