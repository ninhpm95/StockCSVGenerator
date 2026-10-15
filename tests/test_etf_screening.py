"""Check screening preserves source values and last week's annotations."""
import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

screening = importlib.import_module("tasks.etf_screening.run")


class EtfScreeningTests(unittest.TestCase):
    def test_source_values_survive_screening_and_bought_recovery(self):
        source = pd.DataFrame([
            [" 1001 ", "Winner", "100,000,000.25", "0.15%(*3)", "001", "old", "old"],
            ["1002", "Higher fee", "100000000", "0.20%", "002", "", ""],
            ["1003", "Owned excluded", "1,234.50", "0.30%(*3)(*1)", "003", "old", "old"],
            ["1004", "Below threshold", "29,999,999.9", "0.10%", "004", "", ""],
            ["1005", "Ungrouped", "40,000,000.00", "0.25%(*1)", "NULL", "old", "old"],
        ], columns=["Ticker", "Name", "Notional Volume", "Fee", "Extra", "Bought", "Note"])
        current = pd.DataFrame([
            ["1001", "", "Last week's note"],
            [" 1003 ", "yes", "Owned note"],
            ["1005", "", "NA"],
        ], columns=["Ticker", "Bought", "Note"])

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            source.to_csv(root / "full.csv", index=False)
            current.to_csv(root / "current.csv", index=False)
            with patch.multiple(
                screening,
                DATA_CSV=root / "full.csv",
                CURRENT_CSV=root / "current.csv",
                LOOKUP_CSV=root / "full.csv",
                OUTPUT_CSV=root / "current.csv",
                TICKER_GROUPS={"Group": ["1001", "1002"]},
                EXCLUDED_TICKERS=["1003"],
                FEE_TEXT_TO_REMOVE=["(*3)"],
            ), contextlib.redirect_stdout(io.StringIO()):
                screening.run()
            result = pd.read_csv(root / "current.csv", dtype=str, keep_default_na=False)

        expected = source.iloc[[0, 2, 4]].reset_index(drop=True)
        expected["Fee"] = ["0.15%", "0.30%(*1)", "0.25%(*1)"]
        expected["Bought"] = ["", "yes", ""]
        expected["Note"] = ["Last week's note", "Owned note", "NA"]
        pd.testing.assert_frame_equal(result[expected.columns], expected)

    def test_complementary_duplicate_annotations_are_combined(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "current.csv"
            path.write_text("Ticker,Bought,Note\n1001,,NA\n 1001 ,yes,\n", encoding="utf-8")
            annotations = screening.load_annotations(path)
        self.assertEqual(annotations.to_dict("records"), [
            {"Ticker": "1001", "Bought": "yes", "Note": "NA"},
        ])

    def test_conflicting_annotations_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "current.csv"
            path.write_text("Ticker,Bought,Note\n1001,,first\n1001,yes,second\n", encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Conflicting Note"):
                screening.load_annotations(path)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_bought_ticker_leaves_current_file_untouched(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            full = root / "full.csv"
            current = root / "current.csv"
            full.write_text("Ticker,Name,Notional Volume,Fee\n1001,ETF,40000000,0.1\n", encoding="utf-8")
            current.write_text("Ticker,Bought,Note\n9999,yes,Keep this\n", encoding="utf-8")
            before = current.read_bytes()
            with patch.multiple(
                screening, DATA_CSV=full, LOOKUP_CSV=full,
                CURRENT_CSV=current, OUTPUT_CSV=current,
                TICKER_GROUPS={}, EXCLUDED_TICKERS=[],
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "Bought tickers missing"):
                    screening.run()
            self.assertEqual(current.read_bytes(), before)

    def test_failed_write_or_replace_leaves_destination_untouched(self):
        for failure in ("write", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                path = Path(directory) / "current.csv"
                path.write_bytes(b"original contents")
                result = pd.DataFrame({"Ticker": ["1001"]})

                def partial_write(stream, **kwargs):
                    stream.write("partial contents")
                    raise OSError("write failed")

                target = patch.object(result, "to_csv", side_effect=partial_write) if failure == "write" else patch.object(screening.os, "replace", side_effect=OSError("replace failed"))
                with target, self.assertRaises(OSError):
                    screening.write_output(result, path)
                self.assertEqual(path.read_bytes(), b"original contents")
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_fees_are_not_candidates(self):
        from tasks.etf_screening.helper import parse_number
        for value in ("inf", "-inf", "nan", "-0.1%"):
            with self.subTest(value=value):
                self.assertIsNone(parse_number(value))

    def test_selection_does_not_depend_on_dataframe_index(self):
        source = pd.DataFrame({
            "Ticker": ["1001", "1002"], "Name": ["First", "Second"],
            "Notional Volume": ["40000000", "50000000"], "Fee": ["0.1", "0.2"],
        }, index=[80, 20])
        annotations = pd.DataFrame(columns=["Ticker", "Bought", "Note"])
        with patch.multiple(screening, TICKER_GROUPS={}, EXCLUDED_TICKERS=[]):
            positions, _ = screening.select_rows(source, annotations, {})
        result = screening.build_output(source, positions, annotations)
        self.assertEqual(result["Ticker"].tolist(), ["1001", "1002"])


if __name__ == "__main__":
    unittest.main()
