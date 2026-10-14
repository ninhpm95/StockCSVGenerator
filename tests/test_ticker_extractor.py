"""Regression checks for extraction from the ETF cleanser's CSV contract."""
import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

extractor = importlib.import_module("tasks.ticker_extractor.run")
HEADER = "Ticker,Name,ISIN,Weight(%),Exchange\n"
OUTPUT_COLUMNS = ["Ticker", "Name", "ISIN", "Exchange"]


class TickerExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".ticker-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selection_patch = patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", False)
        self.selection_patch.start()
        self.addCleanup(self.selection_patch.stop)
        self.threshold_patch = patch.object(extractor, "MIN_HOLDINGS_FOR_WEIGHT_FILTER", 0)
        self.threshold_patch.start()
        self.addCleanup(self.threshold_patch.stop)
        extractor.load_exchange_countries.cache_clear()
        self.addCleanup(extractor.load_exchange_countries.cache_clear)
        self.exchanges = self.root / "exchanges.csv"
        self.exchanges.write_text(
            "exchange,short_name,country\n"
            "Tokyo Stock Exchange,TSE,JP\n"
            "Hong Kong Exchanges And Clearing Ltd,HKEX,HK\n"
            "Toronto Stock Exchange,TSX,CA\n"
            "NASDAQ Stock Market,NASDAQ,US\n"
            "XNAS,NASDAQ,US\n"
            "Euronext Paris,Euronext,FR\n"
            "Euronext Brussels,Euronext,BE\n", encoding="utf-8")
        self.mapping_patch = patch.object(extractor, "EXCHANGES_PATH", self.exchanges)
        self.mapping_patch.start()
        self.addCleanup(self.mapping_patch.stop)

    def holdings(self, text, name="ETF_JP_test.csv"):
        path = self.root / name
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_numeric_top_holdings_with_blanks_and_leading_zero_ticker(self):
        path = self.holdings(HEADER +
                             ",,,,\n"
                             "NA,National Bank,CA6330671034,2,TSX\n"
                             "'00175,Geely,KYG3777B1032,85,HKEX\n"
                             "3994,Money Forward,JP3869960009,,TSE\n")
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            result = extractor.extract_holdings(path)
        self.assertIsNone(result.problem)
        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.records[0]["Ticker"], "'00175")
        self.assertEqual(result.records[0]["Region"], "HK")
        self.assertFalse(result.weight_undetermined)

    def test_missing_weights_keep_all_rows_and_exchange_region(self):
        path = self.holdings(HEADER + "3994,Money Forward,,,TSE\n7203,Toyota,,,TSE\n")
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            result = extractor.extract_holdings(path)
        self.assertTrue(result.weight_undetermined)
        self.assertEqual(result.records[0]["Ticker"], "3994")
        self.assertEqual(result.records[0]["Region"], "JP")
        self.assertEqual(len(result.records), 2)

    def test_missing_ticker_and_exchange_columns_skip_ineligible_rows(self):
        result = extractor.extract_holdings(self.holdings("Code,Name,ISIN\n3994,Money Forward,JP3869960009\n"))
        self.assertEqual(result.problem, "no holdings with both ticker and exchange")
        self.assertEqual(result.skipped_incomplete, 1)

    def test_missing_optional_columns_are_blank_and_extra_columns_are_ignored(self):
        result = extractor.extract_holdings(self.holdings(
            "Extra,Ticker,Exchange,Another\nignored,3994,TSE,ignored\n"))
        self.assertIsNone(result.problem)
        self.assertEqual(result.records, [{
            "Ticker": "3994", "Name": "", "ISIN": "", "Exchange": "TSE", "Region": "JP",
        }])

    def test_missing_weight_with_filter_enabled_keeps_all_eligible_rows(self):
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            result = extractor.extract_holdings(self.holdings(
                "Ticker,Exchange\n3994,TSE\n7203,TSE\n"))
        self.assertEqual(len(result.records), 2)
        self.assertTrue(result.weight_undetermined)

    def test_flag_off_keeps_more_than_100_holdings_without_weights(self):
        path = self.holdings(HEADER + "".join(
            f"T{i},Holding {i},US{i:010d},,XNAS\n" for i in range(120)
        ))
        result = extractor.extract_holdings(path)
        self.assertEqual(len(result.records), 120)
        self.assertFalse(result.weight_undetermined)

    def test_weight_selection_includes_crossing_holding_and_sorts_numerically(self):
        path = self.holdings(HEADER +
                             "A,Small,US0000000001,10,XNAS\n"
                             "B,Medium,US0000000002,20,XNAS\n"
                             "C,Largest,US0000000003,40,XNAS\n"
                             "D,Large,US0000000004,30,XNAS\n")
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            result = extractor.extract_holdings(path)
        self.assertEqual([row["Ticker"] for row in result.records], ["B", "C", "D"])
        self.assertTrue(all("Weight(%)" not in row for row in result.records))
        self.assertEqual(result.selected_rows, 3)

    def test_small_files_skip_weight_selection_and_threshold_is_inclusive(self):
        for count, expected in ((299, 299), (300, 1)):
            with self.subTest(count=count):
                path = self.holdings(HEADER + "".join(
                    f"T{i},Holding {i},,{80 if i == 0 else 0.01},XNAS\n"
                    for i in range(count)
                ) + ",,,,\n")
                with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True), \
                     patch.object(extractor, "MIN_HOLDINGS_FOR_WEIGHT_FILTER", 300), \
                     patch.object(extractor, "select_top_indices", wraps=extractor.select_top_indices) as select:
                    result = extractor.extract_holdings(path)
                self.assertEqual(result.total_rows, count)
                self.assertEqual(result.selected_rows, expected)
                self.assertEqual(select.call_count, int(count >= 300))
                self.assertFalse(result.weight_undetermined)

    def test_weight_filter_threshold_is_configurable(self):
        path = self.holdings(HEADER + "A,First,,80,XNAS\nB,Second,,20,XNAS\n")
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            with patch.object(extractor, "MIN_HOLDINGS_FOR_WEIGHT_FILTER", 3):
                self.assertEqual(extractor.extract_holdings(path).selected_rows, 2)
            with patch.object(extractor, "MIN_HOLDINGS_FOR_WEIGHT_FILTER", 2):
                self.assertEqual(extractor.extract_holdings(path).selected_rows, 1)

    def test_exact_threshold_ties_and_floating_point_boundary(self):
        self.assertEqual(extractor.select_top_indices([50, 30, 20]), ([0, 1], True))
        self.assertEqual(extractor.select_top_indices([20] * 5), ([0, 1, 2, 3], True))
        self.assertEqual(extractor.select_top_indices([0.1] * 1000), (list(range(800)), True))

    def test_unreachable_threshold_keeps_all_without_rescaling_or_invalid_weights(self):
        for weights in ([40, 20, 10], [None, 0, -1], [float("nan"), float("inf"), 50]):
            with self.subTest(weights=weights):
                self.assertEqual(extractor.select_top_indices(weights), (list(range(len(weights))), False))
        self.assertEqual(extractor.select_top_indices([None, 50, 30]), ([1, 2], True))

    def test_summary_reports_weight_filter_and_fallback(self):
        path = self.root / "ETF.csv"
        summary = extractor.RunSummary()
        summary.add(path, extractor.FileResult([], total_rows=4, selected_rows=3))
        self.assertIn("top 80% weight: 3 of 4", summary.partial[0])
        summary.add(path, extractor.FileResult([], total_rows=4, selected_rows=4, weight_undetermined=True))
        self.assertIn("could not reach 80%; kept all 4 eligible", summary.partial[1])

    def test_run_preserves_source_deduplicates_and_cleans_stale_outputs(self):
        inputs = self.root / "holdings"
        inputs.mkdir()
        self.holdings(HEADER + "'00175,Geely,KYG3777B1032,10,HKEX\n", "first.csv").rename(inputs / "first.csv")
        self.holdings(HEADER + "'00175,Geely,KYG3777B1032,9,HKEX\nNA,National Bank,,8,TSX\n", "second.csv").rename(inputs / "second.csv")
        (inputs / "ignored.xlsx").write_text("not a cleansed CSV", encoding="utf-8")
        output = self.root / "output"
        output.mkdir()
        (output / "US.csv").write_text("stale", encoding="utf-8")
        (output / "KY.csv").write_text("old ISIN-country output", encoding="utf-8")
        (output / "notes.csv").write_text("old notes", encoding="utf-8")
        (output / "old").mkdir()
        (output / "old" / "notes.txt").write_text("old notes", encoding="utf-8")
        with patch.object(extractor, "HOLDINGS_DIR", inputs), \
             patch.object(extractor, "OUTPUT_DIR", output), \
             contextlib.redirect_stdout(io.StringIO()):
            extractor.run()
        hk = pd.read_csv(output / "HK.csv", dtype=str, keep_default_na=False)
        ca = pd.read_csv(output / "CA.csv", dtype=str, keep_default_na=False)
        self.assertEqual(len(hk), 1)
        self.assertEqual(hk.iloc[0]["Ticker"], "'00175")
        self.assertEqual(list(hk.columns), OUTPUT_COLUMNS)
        self.assertEqual(ca.iloc[0]["Ticker"], "NA")
        self.assertEqual(ca.iloc[0]["ISIN"], "")
        self.assertFalse((output / "US.csv").exists())
        self.assertFalse((output / "KY.csv").exists())
        self.assertEqual({p.name for p in output.iterdir()}, {"HK.csv", "CA.csv"})

    def test_european_isin_on_us_exchange_and_unknown_exchange(self):
        result = extractor.extract_holdings(self.holdings(
            HEADER + "ASML,ASML,NL0010273215,9,XNAS\n"
            "ASML,ASML,NL0010273215,8,Unrecognized Exchange\n"
            "ASML,ASML,NL0010273215,7,\n"))
        self.assertEqual([row["Region"] for row in result.records], ["US", "UNKNOWN"])
        self.assertEqual(extractor.get_region("nasdaq stock market"), "US")

    def test_merge_deduplicates_tickers_without_using_isin_and_keeps_countries_separate(self):
        inputs = self.root / "holdings"
        inputs.mkdir()
        (inputs / "a.csv").write_text(
            HEADER + "ABC,First,US0000000001,10,XNAS\n"
            "OLD,Old ticker,US0000000002,11,XNAS\n"
            ",ISIN only one,US0000000003,12,XNAS\n"
            ",ISIN only two,US0000000005,13,XNAS\n", encoding="utf-8")
        (inputs / "b.csv").write_text(
            HEADER + "ABC,Last,US0000000004,2,XNAS\n"
            "NEW,New ticker,US0000000002,3,XNAS\n"
            "ABC,Other country,US0000000004,4,TSE\n", encoding="utf-8")
        output = self.root / "output"
        with patch.object(extractor, "HOLDINGS_DIR", inputs), \
             patch.object(extractor, "OUTPUT_DIR", output), \
             contextlib.redirect_stdout(io.StringIO()):
            extractor.run()
        us = pd.read_csv(output / "US.csv", dtype=str, keep_default_na=False)
        jp = pd.read_csv(output / "JP.csv", dtype=str, keep_default_na=False)
        self.assertEqual(list(us.columns), OUTPUT_COLUMNS)
        self.assertEqual(len(us), 3)
        self.assertEqual(set(us["Ticker"]), {"ABC", "NEW", "OLD"})
        abc = us.loc[us["Ticker"] == "ABC"].iloc[0]
        self.assertEqual((abc["Name"], abc["ISIN"]),
                         ("First", "US0000000001"))
        self.assertEqual(jp.iloc[0]["Ticker"], "ABC")

    def test_ambiguous_short_exchange_name_stays_unknown(self):
        self.assertEqual(extractor.get_region("Euronext Paris"), "FR")
        self.assertEqual(extractor.get_region("Euronext Brussels"), "BE")
        self.assertEqual(extractor.get_region("Euronext"), "UNKNOWN")

    def test_same_country_keeps_different_exchanges_and_deduplicates_matching_pairs(self):
        inputs = self.root / "holdings"
        inputs.mkdir()
        (inputs / "a.csv").write_text(
            "Ticker,Name,Exchange\nABC,First,XNAS\nABC,Other exchange,NASDAQ\n",
            encoding="utf-8")
        (inputs / "b.csv").write_text(
            "Ticker,Name,Exchange\n abc ,Last, xnas \n",
            encoding="utf-8")
        output = self.root / "output"
        with patch.object(extractor, "HOLDINGS_DIR", inputs), \
             patch.object(extractor, "OUTPUT_DIR", output), \
             contextlib.redirect_stdout(io.StringIO()):
            extractor.run()
        us = pd.read_csv(output / "US.csv", dtype=str, keep_default_na=False)
        self.assertEqual(len(us), 2)
        self.assertEqual(set(us["Name"]), {"First", "Other exchange"})
        first = us.loc[us["Name"] == "First"].iloc[0]
        self.assertEqual((first["Ticker"], first["Exchange"]), ("ABC", "XNAS"))

    def test_dedup_prefers_more_filled_output_fields_and_keeps_first_on_ties(self):
        inputs = self.root / "holdings"
        inputs.mkdir()
        (inputs / "a.csv").write_text(
            "Ticker,Name,ISIN,Exchange,Extra\n"
            "A,Incomplete,,XNAS,ignored\n"
            "B,Complete,US0000000002,XNAS,ignored\n"
            "C,First,,XNAS,ignored\n"
            "C,Same file tie,,XNAS,ignored\n"
            "D, -,N/A,XNAS,ignored\n", encoding="utf-8")
        (inputs / "b.csv").write_text(
            "Ticker,Name,ISIN,Exchange\n"
            "A,More complete,US0000000001,XNAS\n"
            "B,Less complete,,XNAS\n"
            "C,Later tie,,XNAS\n"
            "D,Valid name,,XNAS\n", encoding="utf-8")
        output = self.root / "output"
        with patch.object(extractor, "HOLDINGS_DIR", inputs), \
             patch.object(extractor, "OUTPUT_DIR", output), \
             contextlib.redirect_stdout(io.StringIO()):
            extractor.run()
        rows = pd.read_csv(output / "US.csv", dtype=str, keep_default_na=False).set_index("Ticker")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows.loc["A", "Name"], "More complete")
        self.assertEqual(rows.loc["A", "ISIN"], "US0000000001")
        self.assertEqual(rows.loc["B", "Name"], "Complete")
        self.assertEqual(rows.loc["C", "Name"], "First")
        self.assertEqual(rows.loc["D", "Name"], "Valid name")

    def test_only_ticker_and_exchange_are_required_and_values_are_preserved(self):
        result = extractor.extract_holdings(self.holdings(
            HEADER + "BG,BAWAG GROUP AG,AT0000BAWAG2,0.02,TSE\n"
            "EBS,ERSTE GROUP BANK AG,AT0000652011,0.05,TSE\n"
            "CUSTOM,,,4,Unmapped exchange\n"
            "MRK,Merck,US58933Y1055,20,\n"
            ",ISIN only,US0000000001,30,XNAS\n"
            "   ,Whitespace,,5,XNAS\n"))
        self.assertEqual([row["Ticker"] for row in result.records], ["BG", "EBS", "CUSTOM"])
        self.assertEqual(result.records[0]["ISIN"], "AT0000BAWAG2")
        self.assertNotIn("Weight(%)", result.records[0])
        self.assertEqual(result.records[2]["ISIN"], "")
        self.assertEqual(result.records[2]["Name"], "")
        self.assertEqual(result.records[2]["Exchange"], "Unmapped exchange")
        self.assertEqual(result.records[2]["Region"], "UNKNOWN")
        self.assertEqual(result.skipped_incomplete, 3)

    def test_ineligible_rows_do_not_consume_weight_threshold(self):
        path = self.holdings(HEADER + "MRK,No exchange,,90,\nA,Stock,,50,XNAS\nB,Stock,,30,XNAS\nC,Stock,,20,XNAS\n")
        with patch.object(extractor, "FILTER_BY_CUMULATIVE_WEIGHT", True):
            result = extractor.extract_holdings(path)
        self.assertEqual([row["Ticker"] for row in result.records], ["A", "B"])
        self.assertEqual(result.skipped_incomplete, 1)

    def test_missing_country_column_fails_clearly(self):
        self.exchanges.write_text("exchange,short_name\nTokyo Stock Exchange,TSE\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing columns: country"):
            extractor.load_exchange_countries()


if __name__ == "__main__":
    unittest.main()
