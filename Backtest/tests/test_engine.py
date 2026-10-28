import unittest
from datetime import date

from backtest.engine import Bar, Config, Event, anniversary, backtest
from backtest.sources import normalize_slushfund, official_url


def event(filed="2023-01-02", ticker="ABC", key="one"):
    return Event(key, "Example Filer", ticker, date(2022, 12, 15),
                 date.fromisoformat(filed),
                 "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2023/123.pdf")


def bar(open_price=100, high=None, close=None):
    high = open_price if high is None else high
    close = open_price if close is None else close
    return Bar(open_price, max(open_price, high, close), min(open_price, close), close)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.days = [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)]
        self.prices = {x: {d: bar(100) for d in self.days} for x in ("ABC", "SPY", "QQQ")}

    def run_sim(self, events=None, **kwargs):
        return backtest(events or [event()], self.prices, date(2023, 1, 1),
                        self.days[-1], Config(slippage_bps=0, **kwargs))

    def test_no_lookahead_2023_start_or_future_filing(self):
        # Jan 2 was a holiday; the Jan 2 filing can only be bought Jan 3.
        r = self.run_sim()
        self.assertEqual(r["open_positions"][0]["entry_date"], "2023-01-03")
        self.assertEqual(r["entered_positions"], 1)
        # A Jan 4 filing cannot be anticipated on Jan 3.
        r = self.run_sim([event("2023-01-04")])
        self.assertEqual(r["open_positions"][0]["entry_date"], "2023-01-05")

    def test_prior_year_filing_does_not_enter_after_start(self):
        with self.assertRaises(ValueError):
            self.run_sim([event("2022-12-30")])

    def test_take_profit_on_intraday_high(self):
        self.prices["ABC"][self.days[1]] = bar(100, high=111, close=105)
        r = self.run_sim()
        self.assertEqual(r["closed_trades"], 1)
        self.assertEqual(r["trades"][0]["reason"], "TAKE_PROFIT_LIMIT")
        self.assertAlmostEqual(r["trades"][0]["exit_price"], 110)

    def test_gap_exit_at_open_not_target(self):
        self.prices["ABC"][self.days[1]] = bar(125)
        r = self.run_sim()
        self.assertEqual(r["trades"][0]["reason"], "TAKE_PROFIT_GAP")
        self.assertEqual(r["trades"][0]["exit_price"], 125)

    def test_no_duplicate_held_ticker(self):
        r = self.run_sim([event(), event("2023-01-02", key="another")])
        self.assertEqual(r["entered_positions"], 1)
        self.assertEqual(r["diagnostics"]["duplicate_held_symbol"], 1)

    def test_calendar_year_exit(self):
        self.days.extend([date(2024, 1, 3), date(2024, 1, 4)])
        for prices in self.prices.values():
            for day in self.days[-2:]:
                prices[day] = bar(100)
        r = self.run_sim()
        self.assertEqual(r["trades"][0]["reason"], "MAX_ONE_YEAR")
        self.assertEqual(r["trades"][0]["exit_date"], "2024-01-03")
        self.assertEqual(anniversary(date(2024, 2, 29)), date(2025, 2, 28))

    def test_weekend_expiry_uses_last_session_before_anniversary(self):
        days = [date(2023, 1, 5), date(2023, 1, 6),
                date(2024, 1, 5), date(2024, 1, 8)]
        prices = {symbol: {d: bar(100) for d in days}
                  for symbol in ("ABC", "SPY", "QQQ")}
        r = backtest([event("2023-01-05")], prices, days[0], days[-1],
                     Config(slippage_bps=0))
        self.assertEqual(r["trades"][0]["exit_date"], "2024-01-05")

    def test_data_integrity(self):
        self.assertFalse(official_url("https://disclosures-clerk.house.gov.evil.com/a"))
        row = {"Transaction code": "BUY", "Instrument": "Stock",
               "Asset type (as filed)": "Stock", "Ticker": "ABC", "Member": "Official",
               "Trade date": "2023-01-03", "First report filed": "2023-01-02",
               "Filing URL (first report)": "https://disclosures-clerk.house.gov/a.pdf"}
        records, rejected = normalize_slushfund([row])
        self.assertEqual(records, [])
        self.assertEqual(rejected["date_anomaly"], 1)


if __name__ == "__main__":
    unittest.main()
