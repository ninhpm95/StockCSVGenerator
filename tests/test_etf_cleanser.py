"""Regression checks: python -B -m unittest discover -s tests -v."""
import contextlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tasks.etf_cleanser.config import Config
from tasks.etf_cleanser.instruments import instrument_kind
from tasks.etf_cleanser.lookup import AmbiguousLookupError, GlobalLookup
from tasks.etf_cleanser.models import Job, Row, Table
from tasks.etf_cleanser.parsing import read_csv, sniff_delimiter
from tasks.etf_cleanser.run import plan_output_names, process_jobs, publish_output, run
from tasks.etf_cleanser.steps import (step060_unify_exchange_names, step070_refresh_from_isin,
                                    step080_isin_backfill, step090_drop_incomplete)


HEADER = "Code,Name,ISIN,Exchange,Shares Amount,Stock Price\n"
ISIN = "JP3869960009"


class CleanserTests(unittest.TestCase):
    def setUp(self):
        # Explicit workspace location avoids redirected Windows temp folders.
        self.temp = tempfile.TemporaryDirectory(prefix=".etf-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.output = self.root / "output"
        self.input.mkdir()
        self.output.mkdir()
        self.metadata = self.root / "etfs.csv"
        self.metadata.write_text("Ticker,Region,Company,ShortName\n1306,JP,Example,topix\n", encoding="utf-8")
        self.lookup = self.root / "lookup.csv"
        self.lookup.write_text(f"ticker,name,exchange,isin,asset_type\n3994,MONEY FORWARD,TSE,{ISIN},Stock\n", encoding="utf-8")
        self.exchanges = self.root / "exchanges.csv"
        self.exchanges.write_text("exchange,short_name,lookup_name\nTokyo Stock Exchange,TSE,TSE\n", encoding="utf-8")
        self.cfg = replace(Config(), input_dir=self.input, output_dir=self.output,
                           etf_list_path=self.metadata, lookup_path=self.lookup,
                           exchanges_path=self.exchanges,
                           enable_manual_updates=False)

    def input_file(self, text, name="1306.csv"):
        path = self.input / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_forward_company_names_remain_securities(self):
        for name in ("MONEY FORWARD", "MONEY FORWARD INC", "FORWARD AIR CORP"):
            for isin in (None, ISIN):
                with self.subTest(name=name, isin=isin):
                    self.assertEqual(instrument_kind(Row(code="3994", name=name, isin=isin)), "security")
        for isin in (None, ISIN):
            self.assertEqual(instrument_kind(Row(code="FWRD", name="FORWARD AIR CORP", isin=isin)), "security")

    def test_observed_forward_contracts_remain_forwards(self):
        for row in (Row(isin="FORWARD", name="2026-09-04"),
                    Row(name="FWRDUSDJPY01"), Row(code="FWRDUSDJPY01")):
            self.assertEqual(instrument_kind(row), "forward")

    def test_money_forward_isin_is_backfilled(self):
        row = Row(code="3994", name="MONEY FORWARD", exchange="TSE")
        job = Job(path=Path("1475_holdings.csv"), table=Table(rows=[row]))
        step080_isin_backfill(job, self.cfg)
        self.assertEqual(row.isin, ISIN)

    def test_lookup_name_matches_without_replacing_holding_short_name(self):
        self.exchanges.write_text(
            "exchange,short_name,lookup_name\n"
            "Nyse Euronext - Euronext Paris,Euronext Paris,Euronext\n"
            "XPAR,Euronext Paris,Euronext\n"
            "Nyse Euronext - Euronext Brussels,Euronext Brussels,Euronext\n",
            encoding="utf-8")
        self.lookup.write_text(
            "ticker,name,exchange,isin,asset_type\n"
            "ABC,Example,Euronext,FR0000000001,Stock\n", encoding="utf-8")
        for exchange in ("Nyse Euronext - Euronext Paris", "XPAR", "Euronext Paris",
                         "Nyse Euronext - Euronext Brussels"):
            with self.subTest(exchange=exchange):
                short_name = ("Euronext Brussels" if "Brussels" in exchange else "Euronext Paris")
                rows = [Row(code="OLD", isin="FR0000000001", exchange=exchange),
                        Row(code="ABC", exchange=exchange)]
                job = Job(path=Path("1306.csv"), table=Table(rows=rows))
                step060_unify_exchange_names(job, self.cfg)
                step070_refresh_from_isin(job, self.cfg)
                step080_isin_backfill(job, self.cfg)
                self.assertEqual([(r.code, r.isin, r.exchange) for r in rows],
                                 [("ABC", "FR0000000001", short_name)] * 2)

    def test_lookup_accepts_raw_short_and_lookup_exchange_names(self):
        self.exchanges.write_text(
            "exchange,short_name,lookup_name\nXPAR,Euronext Paris,Euronext\n",
            encoding="utf-8")
        self.lookup.write_text(
            "ticker,exchange,isin\nABC,Euronext,FR0000000001\n", encoding="utf-8")
        lookup = GlobalLookup(self.lookup, self.exchanges)
        for exchange in ("XPAR", " Euronext Paris ", "euronext"):
            with self.subTest(exchange=exchange):
                self.assertEqual(lookup.find(ticker="ABC", exchange=exchange).isin,
                                 "FR0000000001")
        self.assertIsNone(lookup.find(ticker="ABC", exchange="Unknown"))

    def test_isin_only_holding_survives_cleanup(self):
        row = Row(isin=ISIN, exchange="TSE", weight="5")
        job = Job(path=Path("1306.csv"), table=Table(rows=[Row(), row, Row(name="Total")]))
        step090_drop_incomplete(job, self.cfg)
        self.assertEqual(job.table.rows, [row])
        self.assertEqual(job.dropped_rows, 2)

    def test_empty_and_summary_only_inputs_fail(self):
        for body in ("", ",Total,,,,\n"):
            with self.subTest(body=body):
                job = Job(path=self.input_file(HEADER + body))
                self.assertEqual(process_jobs([job], self.cfg), 1)
                self.assertIn("no holdings", job.error)
                self.assertEqual(list(self.output.iterdir()), [])

    def test_failed_run_preserves_previous_output(self):
        self.input_file(HEADER)
        previous = self.output / "previous.csv"
        previous.write_text("previous holdings", encoding="utf-8")
        with patch("tasks.etf_cleanser.run.Config", return_value=self.cfg):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                run([])
        self.assertEqual(exc.exception.code, 1)
        self.assertEqual(previous.read_text(encoding="utf-8"), "previous holdings")
        self.assertEqual(list(self.root.glob(".cleanser-*")), [])

    def test_delimiters_respect_quoted_numbers_and_multiline_names(self):
        for delimiter in (",", ";", "\t"):
            with self.subTest(delimiter=delimiter):
                header = delimiter.join(HEADER.strip().split(",")) + "\n"
                body = delimiter.join(["3994", '"Example, Inc\nsecond line"', ISIN,
                                       "TSE", '"1,234,567,890"', '"1,234,567,890"']) + "\n"
                text = "Provider title\n" + header + body * 9
                self.assertEqual(sniff_delimiter(text), delimiter)
                grid = read_csv(self.input_file(text))
                self.assertEqual(len(grid.rows[2]), 6)
                self.assertEqual(grid.rows[2][4], "1,234,567,890")

    def test_output_is_written_directly_to_metadata_name(self):
        job = Job(path=self.input_file(HEADER + f"3994,MONEY FORWARD,{ISIN},TSE,10,5\n"))
        plan_output_names([job], self.cfg)
        self.assertEqual(process_jobs([job], self.cfg), 0)
        self.assertEqual([p.name for p in self.output.iterdir()], ["1306_JP_Example_topix.csv"])

    def test_final_name_collisions_fail_preflight(self):
        jobs = [Job(path=Path(name)) for name in ("1306.csv", "1306_holdings.xlsx")]
        with self.assertRaisesRegex(ValueError, "output filename collision"):
            plan_output_names(jobs, self.cfg)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_successful_run_publishes_final_name_and_removes_stale_output(self):
        self.input_file(HEADER + f"3994,MONEY FORWARD,{ISIN},TSE,10,5\n")
        (self.output / "stale.csv").write_text("old", encoding="utf-8")
        with patch("tasks.etf_cleanser.run.Config", return_value=self.cfg):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
                run([])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual([p.name for p in self.output.iterdir()], ["1306_JP_Example_topix.csv"])
        self.assertEqual(list(self.root.glob(".cleanser-*")), [])

    def test_unmatched_filename_collision_is_case_insensitive(self):
        jobs = [Job(path=Path(name)) for name in ("Unknown.csv", "unknown.xlsx")]
        with self.assertRaisesRegex(ValueError, "output filename collision"):
            plan_output_names(jobs, self.cfg)

    def test_unmatched_file_retains_name(self):
        job = Job(path=Path("Unknown_holdings.xlsx"))
        plan_output_names([job], self.cfg)
        self.assertEqual(job.output_filename, "Unknown_holdings.csv")

    def test_ambiguous_lookup_retains_multiple_listings(self):
        self.lookup.write_text(f"ticker,exchange,isin\n3994,TSE,{ISIN}\n3995,TSE,{ISIN}\n", encoding="utf-8")
        lookup = GlobalLookup(self.lookup)
        with self.assertRaises(AmbiguousLookupError):
            lookup.find(isin=ISIN, exchange="TSE")
        self.assertEqual(lookup.find(isin=ISIN, exchange="TSE", preferred_ticker="3994").ticker, "3994")

    def test_publication_failure_restores_previous_directory(self):
        previous = self.output / "previous.csv"
        previous.write_text("old", encoding="utf-8")
        staged = self.root / "staged"
        staged.mkdir()
        (staged / "new.csv").write_text("new", encoding="utf-8")
        backup = self.root / ".cleanser-previous-test"

        def rename(source, destination):
            if source == staged:
                raise PermissionError("simulated publication lock")
            source.rename(destination)

        with patch("tasks.etf_cleanser.run.rename_with_retry", side_effect=rename):
            with self.assertRaises(PermissionError):
                publish_output(staged, self.output, backup)
        self.assertEqual(previous.read_text(encoding="utf-8"), "old")
        self.assertTrue((staged / "new.csv").exists())
        self.assertFalse(backup.exists())

    def test_advisory_checks_always_run_and_log_findings(self):
        job = Job(path=self.input_file(HEADER + f"X,Example,{ISIN},TSE,10,5\n"))
        with self.assertLogs("tasks.etf_cleanser", level="WARNING") as logs:
            self.assertEqual(process_jobs([job], self.cfg), 0)
        self.assertTrue(any("ticker format" in finding for finding in job.findings))
        self.assertTrue(any("REVIEW" in message and "ticker format" in message for message in logs.output))

    def test_main_respects_logging_setting(self):
        import main
        for logs in (False, True):
            with self.subTest(logs=logs):
                with patch.object(main, "MODE", main.CLEANSE_ETFS), \
                     patch.dict(main.TASK_RUNNERS, {main.CLEANSE_ETFS: lambda: None}), \
                     patch("tasks.etf_cleanser.config.ENABLE_LOG_FILE", logs), \
                     patch.object(main, "configure_logging", return_value=None) as configure:
                    main.main()
                self.assertEqual(configure.call_args.args[0], logs)


if __name__ == "__main__":
    unittest.main()
