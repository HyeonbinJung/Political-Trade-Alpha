import unittest
from datetime import date
from decimal import Decimal

from political_trade_alpha.core import (
    InvalidFiling, Trade, candidate_notional, evaluate, official_disclosure_url,
)
from political_trade_alpha.cli import scheduled_slot_is_due


def sample(**overrides):
    record = {
        "politician": "Example Official",
        "symbol": "AAPL",
        "asset_type": "STOCK",
        "side": "BUY",
        "trade_date": "2026-10-01",
        "disclosure_date": "2026-10-20",
        "amount_low": 1001,
        "amount_high": 15000,
        "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/example.pdf",
        "verified": True,
        "verification_note": "Example test fixture only; no real transaction claimed",
    }
    record.update(overrides)
    return record


class TradeTests(unittest.TestCase):
    def setUp(self):
        self.now = date(2026, 10, 28)
        self.equity = Decimal("10000")

    def test_official_hosts_only(self):
        self.assertTrue(official_disclosure_url(sample()["source_url"]))
        self.assertTrue(official_disclosure_url("https://efdsearch.senate.gov/search/home/"))
        self.assertTrue(official_disclosure_url("https://extapps2.oge.gov/report.pdf"))
        self.assertFalse(official_disclosure_url("https://oge.gov.evil.example/278t.pdf"))
        self.assertFalse(official_disclosure_url("http://disclosures-clerk.house.gov/x.pdf"))

    def test_strict_buy_stock_verification(self):
        for change in [{"side": "SELL"}, {"asset_type": "BOND"}, {"asset_type": "ETF"},
                       {"verified": False}, {"verification_note": ""},
                       {"source_url": "https://unofficial.example/filing"}]:
            with self.subTest(change=change), self.assertRaises(InvalidFiling):
                Trade.from_mapping(sample(**change))

    def test_invalid_amount_range(self):
        for low, high in [(20000, 1000), (-1, 100), ("NaN", 100), (0, 100)]:
            with self.subTest(low=low, high=high), self.assertRaises(InvalidFiling):
                Trade.from_mapping(sample(amount_low=low, amount_high=high))

    def test_proportional_size_when_uncapped(self):
        small = Trade.from_mapping(sample(amount_low=1000, amount_high=1000))
        large = Trade.from_mapping(sample(amount_low=2000, amount_high=2000))
        x = candidate_notional(small, self.equity, self.now)
        y = candidate_notional(large, self.equity, self.now)
        self.assertGreater(y, x)
        self.assertAlmostEqual(float(y / x), 2.0, delta=0.01)

    def test_delayed_filing_reduces_size(self):
        near = Trade.from_mapping(sample(trade_date="2026-10-19",
                                         disclosure_date="2026-10-20",
                                         amount_low=10000, amount_high=10000))
        late = Trade.from_mapping(sample(trade_date="2026-10-01",
                                         disclosure_date="2026-10-20",
                                         amount_low=10000, amount_high=10000))
        self.assertGreater(candidate_notional(near, self.equity, self.now),
                           candidate_notional(late, self.equity, self.now))

    def test_stale_trade_zero(self):
        old = Trade.from_mapping(sample(trade_date="2026-08-01", disclosure_date="2026-09-10"))
        self.assertEqual(candidate_notional(old, self.equity, self.now), Decimal("0"))
        signals, seen, errors = evaluate([sample(trade_date="2026-08-01",
                                                 disclosure_date="2026-09-10")],
                                          set(), self.equity, self.now)
        self.assertEqual(signals[0].reason, "STALE_OVER_45_DAYS")
        self.assertEqual(len(seen), 1)
        self.assertFalse(errors)

    def test_never_process_same_filing_twice(self):
        records = [sample(amount_low=50000, amount_high=50000)]
        first, seen, _ = evaluate(records, set(), self.equity, self.now)
        second, _, _ = evaluate(records, seen, self.equity, self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_invalid_record_not_marked_seen(self):
        signals, seen, errors = evaluate([sample(verified=False)], set(), self.equity, self.now)
        self.assertEqual(signals, [])
        self.assertEqual(seen, set())
        self.assertEqual(len(errors), 1)

    def test_per_symbol_and_daily_caps(self):
        records = [sample(politician=f"Official {i}", symbol=symbol,
                          amount_low=1_000_000, amount_high=1_000_000,
                          trade_date="2026-10-27", disclosure_date="2026-10-28")
                   for i, symbol in enumerate(["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "META"])]
        signals, _, errors = evaluate(records, set(), self.equity, self.now)
        self.assertFalse(errors)
        self.assertLessEqual(sum(s.paper_notional for s in signals), Decimal("300"))
        self.assertTrue(all(s.paper_notional <= Decimal("100") for s in signals))

    def test_dst_gate(self):
        summer, winter = date(2026, 10, 29), date(2026, 11, 2)
        self.assertTrue(scheduled_slot_is_due("0 13 * * *", summer))
        self.assertFalse(scheduled_slot_is_due("0 14 * * *", summer))
        self.assertTrue(scheduled_slot_is_due("0 14 * * *", winter))
        self.assertFalse(scheduled_slot_is_due("0 13 * * *", winter))
        self.assertTrue(scheduled_slot_is_due("59 15 * * *", summer))
        self.assertTrue(scheduled_slot_is_due("59 16 * * *", winter))
        self.assertTrue(scheduled_slot_is_due("0 18 * * *", summer))
        self.assertTrue(scheduled_slot_is_due("0 19 * * *", winter))


if __name__ == "__main__":
    unittest.main()
