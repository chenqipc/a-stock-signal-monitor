import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from market_data.database import MarketDataDatabase
from tests.test_market_data import sample_bars
from web_app.index_service import IndexMarketService


class IndexMarketServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = MarketDataDatabase(Path(self.temp_dir.name) / "market.db")
        self.bars = sample_bars()
        self.bars.loc[self.bars.index[-1], "trade_time"] = pd.Timestamp("2026-07-16")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_intraday_snapshot_is_refreshed_after_market_close(self):
        with patch.object(MarketDataDatabase, "_utc_now", return_value="2026-07-16T06:49:00+00:00"):
            self.database.save_klines("000001.SH", "D", self.bars, "sina", coverage_end="2026-07-16")
        service = IndexMarketService(self.database, now_provider=lambda: datetime(2026, 7, 16, 17, 0))
        cached = self.database.load_klines("000001.SH", "D")

        self.assertTrue(service._data_needs_refresh("000001.SH", cached, datetime(2026, 7, 16).date()))

    def test_post_close_snapshot_is_treated_as_final_daily_bar(self):
        with patch.object(MarketDataDatabase, "_utc_now", return_value="2026-07-16T07:31:00+00:00"):
            self.database.save_klines("000001.SH", "D", self.bars, "sina", coverage_end="2026-07-16")
        service = IndexMarketService(self.database, now_provider=lambda: datetime(2026, 7, 16, 17, 0))
        cached = self.database.load_klines("000001.SH", "D")

        self.assertFalse(service._data_needs_refresh("000001.SH", cached, datetime(2026, 7, 16).date()))

    def test_known_trading_day_missing_bar_ignores_incorrect_coverage_end(self):
        self.database.save_trade_calendar(
            pd.DataFrame({"calendar_date": ["2026-07-16"], "is_trading_day": [1]}), "seed"
        )
        with patch.object(MarketDataDatabase, "_utc_now", return_value="2026-07-16T07:54:00+00:00"):
            self.database.save_klines("GOLD.SGE", "D", self.bars, "sge", coverage_end="2026-07-16")
        cached = self.database.load_klines("GOLD.SGE", "D")
        cached = cached[cached["trade_time"] < pd.Timestamp("2026-07-16")]
        service = IndexMarketService(self.database, now_provider=lambda: datetime(2026, 7, 17, 10, 0))

        self.assertTrue(service._data_needs_refresh("GOLD.SGE", cached, datetime(2026, 7, 17).date(), False))


if __name__ == "__main__":
    unittest.main()
