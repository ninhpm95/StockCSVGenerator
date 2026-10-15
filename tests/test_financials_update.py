"""Market routing regressions; all provider calls are mocked."""
import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from tasks.financials_update import helper, financials, processor, fields as F

runner = importlib.import_module('tasks.financials_update.run')


class MarketRoutingTests(unittest.TestCase):
    def setUp(self):
        helper.load_exchanges.cache_clear()
        self.addCleanup(helper.load_exchanges.cache_clear)

    def test_exchange_country_precedes_filename_and_unknown_falls_back(self):
        for exchange in ('HKEX', 'xhkg', 'HKG', 'Hong Kong Exchanges And Clearing Ltd'):
            self.assertEqual(helper.get_region('JP_stocks.csv', exchange), 'HK')
        self.assertEqual(helper.get_region('JP_stocks.csv', 'unknown'), 'JP')
        self.assertEqual(helper.get_region('US_stocks.csv'), 'US')
        with self.assertRaises(ValueError):
            helper.get_region('global.csv')

    def test_exchange_specific_suffixes_and_country_fallback(self):
        cases = [('7203', 'JP', 'TSE', '7203.T'),
                 ('7203.T', 'JP', '', '7203.T'),
                 ('00175', 'HK', 'HKEX', '00175.HK'),
                 ('BRK.B', 'US', 'NYSE', 'BRK-B'),
                 ('600000', 'CN', 'SSE', '600000.SS'),
                 ('000001', 'CN', 'SZSE', '000001.SZ'),
                 ('005930', 'KR', 'KRX', '005930.KS'),
                 ('035900', 'KR', 'KOSDAQ', '035900.KQ')]
        for ticker, country, exchange, expected in cases:
            with self.subTest(ticker=ticker, exchange=exchange):
                self.assertEqual(helper.format_ticker_for_yfinance(ticker, country, exchange), expected)
        with self.assertRaisesRegex(ValueError, 'unambiguous yahoo_suffix'):
            helper.format_ticker_for_yfinance('000001', 'CN')
        with self.assertRaises(ValueError):
            helper.format_ticker_for_yfinance('ES', 'US', 'CME')

    def test_csv_controls_provider_mappings(self):
        for code in ('NMS', 'NGM', 'NCM', 'BTS'):
            self.assertEqual(helper.map_exchange(code), 'NASDAQ')
        self.assertEqual(helper.map_exchange('PCX'), 'AMEX')
        self.assertEqual(helper.map_exchange('ASE'), 'AMEX')
        self.assertEqual(helper.map_exchange('JPX'), 'TSE')
        self.assertIsNone(helper.map_exchange('unknown'))
        self.assertEqual(helper.get_tv_screener('HK'), 'hongkong')
        self.assertEqual(helper.get_tv_screener('US'), 'america')
        self.assertEqual(helper.get_tv_screener('DE', 'XETRA'), 'germany')
        rows = tuple(dict(row, yahoo_suffix='.TEST', tv_screener='testmarket', tv_exchange='TEST')
                     if row['country'] == 'JP' else row for row in helper.load_exchanges())
        with patch.object(helper, 'load_exchanges', return_value=rows):
            self.assertEqual(helper.format_ticker_for_yfinance('7203', 'JP'), '7203.TEST')
            self.assertEqual(helper.get_tv_screener('JP'), 'testmarket')
            self.assertEqual(helper.map_exchange('TYO'), 'TEST')

    def test_aliases_have_consistent_configuration(self):
        for row in helper.load_exchanges():
            for alias in (row['exchange'], row['short_name']):
                matches = helper._exchange_rows(alias)
                for field in ('country', 'yahoo_suffix', 'yahoo_dot_replacement', 'tv_screener', 'tv_exchange'):
                    self.assertIsNotNone(helper._unique_value(matches, field), (alias, field))

    def test_mixed_markets_are_grouped_and_results_keep_input_order(self):
        def ticker(symbol):
            return SimpleNamespace(info={'symbol': symbol, 'shortName': symbol,
                                         'exchange': 'UNKNOWN', 'currentPrice': 10},
                                   history=lambda **kwargs: pd.DataFrame())

        calls = []

        def analysis(**kwargs):
            calls.append(kwargs)
            return {symbol: SimpleNamespace(indicators={'Recommend.All': 1})
                    for symbol in kwargs['symbols']}

        with patch.object(financials.yf, 'Ticker', side_effect=ticker) as yahoo, \
                patch.object(financials, 'get_multiple_analysis', side_effect=analysis), \
                patch.object(financials.time, 'sleep'):
            rows = financials.fetch_financials_batch(
                ['7203', 'AAPL', '00175', '6758'], ['JP', 'US', 'HK', 'JP'], F.NORMAL,
                ['TSE', 'NASDAQ', 'HKEX', 'TSE'])
        self.assertEqual([call.args[0] for call in yahoo.call_args_list],
                         ['7203.T', 'AAPL', '00175.HK', '6758.T'])
        self.assertEqual([row[F.NAME] for row in rows], ['7203.T', 'AAPL', '00175.HK', '6758.T'])
        self.assertEqual([call['screener'] for call in calls],
                         ['japan', 'japan', 'america', 'america', 'hongkong', 'hongkong'])
        self.assertEqual(calls[0]['symbols'], ['TSE:7203', 'TSE:6758'])
        self.assertTrue(all(row[F.AVG_RATING_1D] == 1 for row in rows))

    def test_fast_mode_and_yahoo_exchange_fallback(self):
        data = SimpleNamespace(info={'symbol': 'AAPL', 'shortName': 'Apple', 'exchange': 'NMS'},
                               history=lambda **kwargs: pd.DataFrame())
        with patch.object(financials.yf, 'Ticker', return_value=data), \
                patch.object(financials, 'get_multiple_analysis', return_value={}) as tv, \
                patch.object(financials.time, 'sleep'):
            financials.fetch_financials_batch(['AAPL'], 'US', F.NORMAL)
            self.assertEqual(tv.call_args.kwargs['symbols'], ['NASDAQ:AAPL'])
            tv.reset_mock()
            financials.fetch_financials_batch(['AAPL'], 'US', F.FAST)
            tv.assert_not_called()

    def test_exchange_survives_full_update_and_identifiers_stay_strings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / 'JP_stocks.csv'
            path.write_text('Ticker,Exchange,Note\n00175,HKEX,hk\nNA,TSX,ca\n00175,TSE,jp\n00175,HKEX,duplicate\n',
                            encoding='utf-8')
            df = processor.load_and_clean_data(str(path))
            self.assertEqual(df[F.TICKER].tolist(), ['00175', 'NA', '00175'])
            with patch.object(runner, 'OUTPUT_DIR', directory), \
                    patch.object(runner, 'process_batches', return_value=[{}, {}, {}]) as process, \
                    contextlib.redirect_stdout(io.StringIO()):
                runner.run(path.name, F.FAST)
            args = process.call_args.args
            self.assertEqual(args[1], ['HK', 'CA', 'JP'])
            self.assertEqual(args[3], ['HKEX', 'TSX', 'TSE'])
            written = pd.read_csv(path, dtype=str, keep_default_na=False)
            self.assertEqual(written[F.EXCHANGE].tolist(), ['HKEX', 'TSX', 'TSE'])
            self.assertEqual(written[F.NOTE].tolist(), ['hk', 'ca', 'jp'])

    def test_regions_and_exchanges_stay_aligned_across_batches(self):
        tickers = [str(i) for i in range(41)]
        regions = ['JP'] * 39 + ['HK', 'US']
        exchanges = ['TSE'] * 39 + ['HKEX', 'NASDAQ']
        with patch.object(processor, 'fetch_financials_batch',
                          side_effect=lambda symbols, *args: [{'ticker': t} for t in symbols]) as fetch, \
                patch.object(processor.time, 'sleep') as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            result = processor.process_batches(tickers, regions, F.FAST, exchanges)
        self.assertEqual(len(fetch.call_args_list[0].args[0]), 39)
        self.assertEqual(fetch.call_args_list[1].args,
                         (['39', '40'], ['HK', 'US'], F.FAST, ['HKEX', 'NASDAQ']))
        self.assertEqual([row['ticker'] for row in result], tickers)
        sleep.assert_called_once()

    def test_filename_only_update_still_works(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / 'HK_stocks.csv'
            path.write_text('Ticker,Note\n00175,keep\n', encoding='utf-8')
            with patch.object(runner, 'OUTPUT_DIR', directory), \
                    patch.object(runner, 'process_batches', return_value=[{}]) as process, \
                    contextlib.redirect_stdout(io.StringIO()):
                runner.run(path.name, F.FAST)
            self.assertEqual(process.call_args.args, (['00175'], ['HK'], F.FAST, ['']))
            written = pd.read_csv(path, dtype=str, keep_default_na=False)
            self.assertEqual(written[F.TICKER].tolist(), ['00175'])
            self.assertEqual(written[F.NOTE].tolist(), ['keep'])


if __name__ == '__main__':
    unittest.main()
