"""Check cleansed holdings parsing and exchange-based region resolution."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tasks.etf_aggregator.constants import ETF_DIR
from tasks.etf_aggregator.loaders import load_exchange_regions
from tasks.etf_aggregator.matcher import find_region, find_stock
from tasks.etf_aggregator.parsers import parse_holdings


class EtfAggregatorTests(unittest.TestCase):
    def test_cleansed_schema_preserves_ticker_exchange_and_weight(self):
        self.assertEqual(ETF_DIR.name, "cleansed_ETFs")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.csv"
            path.write_text(
                'Ticker,Name,ISIN,Weight(%),Exchange\n'
                '0010,"Example, Inc.",US0378331005,12.5,Nasdaq\n',
                encoding="utf-8",
            )
            holdings = parse_holdings(path)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings.iloc[0]["Code"], "0010")
        self.assertEqual(holdings.iloc[0]["Name"], "Example, Inc.")
        self.assertEqual(holdings.iloc[0]["Exchange"], "NASDAQ")
        self.assertAlmostEqual(holdings.iloc[0]["pct"], 0.125)

    def test_only_exchange_short_names_are_loaded_from_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exchanges.csv"
            path.write_text(
                "exchange,short_name,lookup_name,country\n"
                "Example Exchange,EX,LOOKUP,CA\n"
                "Unlisted,UNLISTED,UNLISTED,\n",
                encoding="utf-8",
            )
            mapping = load_exchange_regions(path)
        self.assertEqual(mapping, {"EX": "CA"})

    def test_exchange_country_takes_priority_over_isin_and_filename(self):
        holding = pd.Series({
            "Exchange": "XETRA", "ISIN": "US0378331005", "_source": "123_US.csv",
        })
        databases = {"DE": pd.DataFrame(), "US": pd.DataFrame()}
        self.assertEqual(find_region(holding, databases), ("DE", "exchange:XETRA"))

    def test_unknown_exchange_still_allows_global_isin_fallback(self):
        stock = pd.DataFrame([{
            "_ticker": "ABC", "_isin": "US0378331005",
        }]).set_index("_ticker", drop=False)
        holding = pd.Series({
            "Code": "OTHER", "Exchange": "UNKNOWN", "ISIN": "US0378331005",
        })
        matched, region, reason = find_stock(holding, {"US": stock})
        self.assertEqual(matched["_ticker"], "ABC")
        self.assertEqual((region, reason), ("US", "isin_search"))


if __name__ == "__main__":
    unittest.main()
