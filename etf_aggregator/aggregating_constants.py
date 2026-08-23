from __future__ import annotations

from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = BASE_DIR.parent / "output"
ETF_DIR = BASE_DIR.parent / "ETFs"
LOGS_DIR = BASE_DIR.parent / "logs"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOGS_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

TARGET_ETF_FILE = OUTPUT_DIR / "JP_ETFs.csv"
OUTPUT_FILE = OUTPUT_DIR / "JP_ETFs_details.csv"

# Only these columns are recalculated from ETF holdings.
# Add/remove columns here as desired.
AGGREGATE_COLUMNS = [
    # "ROA",
    # "Net Margin",
    # "Operating Margin",
    # "Debt To Equity",
    # "Earnings Growth",
    # "Vol 1D",
    # "Vol 3D",
    # "Vol 5D",
    # "Vol 30D",
    # "Dividend yield",
    # "Avg Volume",
    # "Market cap",
    "PE ratio",
    "Forward PE ratio",
    "PB",
    # "Price 1D",
    # "Price 3D",
    # "Price 5D",
    # "Price 7D",
    # "Price 30D",
    "Target high %",
    "Target low %",
    "Target mean %",
    # "Avg Rating 1D",
    # "Avg Rating 7D",
    "Avg Rating Score",
    # "Growth",  # handled separately: derived as PE ratio / Forward PE ratio, see processor._apply_growth
]

# How each AGGREGATE_COLUMNS metric should be combined across a fund's
# holdings. Anything not listed here defaults to a plain weighted average,
# which is correct for things like target price %, and rating score, but
# wrong for ratios like P/E (those need a weighted *harmonic* mean).
#   "weighted_average" - standard weight-weighted mean (default, see aggregator.weighted_average)
#   "harmonic"          - weighted harmonic mean (see aggregator.weighted_harmonic_mean)
#   "skip"              - no known formula yet; leave the value as NaN
AGGREGATION_METHODS = {
    "PE ratio": "harmonic",
    "Forward PE ratio": "harmonic",
    "ROA": "skip",  # TODO: figure out the right way to aggregate ROA across holdings
    "ROE": "skip",  # TODO: figure out the right way to aggregate ROE across holdings
}

# If True, values already present in the ETF CSV are replaced.
# If False, only blank/missing values are filled.
OVERWRITE_EXISTING = True

# Yahoo-style rating score mapping.
# Lower score = better rating.
RATING_THRESHOLDS = [
    (1.5, "Strong Buy"),
    (2.5, "Buy"),
    (3.5, "Hold"),
    (4.5, "Sell"),
    (float("inf"), "Strong Sell"),
]

# Matching priority for locating a holding in the stock database:
#   1. Region info parsed from the holdings filename (see loaders.extract_region_from_filename)
#   2. Region column on the holding row, if present
#   3. Exchange -> region mapping
#   4. ISIN first two characters
#   5. Search all stock files
#
# Add/change exchange mappings here if your files contain other exchange names.
EXCHANGE_TO_REGION = {
    "TSE": "JP",
    "TYO": "JP",
    "JPX": "JP",
    "OSE": "JP",
    "TOKYO STOCK EXCHANGE": "JP",
    "NSE": "IN",
    "BSE": "IN",
    "NASDAQ": "US",
    "NYSE": "US",
    "AMEX": "US",
    "ARCA": "US",
    "HKEX": "HK",
    "HKG": "HK",
    "LSE": "GB",
    "LON": "GB",
    "ASX": "AU",
    "SSE": "CN",
    "SZSE": "CN",
    "KSE": "KR",
    "KRX": "KR",
    "TWSE": "TW",
}

# --------------------------------------------------------------------------
# Holdings file parsing
# --------------------------------------------------------------------------

# Suffix used for regional stock-database files, e.g. "JP_stocks.csv".
STOCK_FILE_SUFFIX = "_stocks.csv"

# Sheet name used in Japanese ETF XLSX holdings files.
HOLDINGS_SHEET_NAME = "保有明細"

# A row is the holdings table header if it contains ALL keywords in one of
# these combinations (case-insensitive substring match across the row's
# cells joined together). Combinations are tried in order: every row in the
# file is checked against combination 1 first, and if no row matches at
# all, combination 2 is tried against every row, then combination 3. Within
# whichever combination matches, the first row in file order wins.
#
# This is deliberately specific (rather than "any code-like + name-like +
# one-of-several-other keywords") so that unrelated metadata rows -- e.g. a
# fund-level summary row like "ETF Code, ETF Name, Shares Outstanding" --
# don't get mistaken for the per-holding table header.
HEADER_KEYWORD_COMBINATIONS = [
    {"銘柄コード", "name", "isin"},
    {"code", "name", "isin"},
    {"code", "name", "weight"},
    {"ticker", "name", "weight"},
]

# Column-name candidates used to identify each field once the header row is
# located. Matching is by exact or substring text match (see
# parsers.find_column) so e.g. "銘柄コード（Code）" is recognized as the Code
# column because it contains the text "Code".
# Exact full-string variants are listed alongside short substrings on
# purpose: a short candidate like "銘柄" would ambiguously substring-match
# both "銘柄コード（Code）" and "銘柄（Name）", so the exact form is tried
# first (find_column checks all candidates for an exact match before
# falling back to substring matching).
CODE_COLUMN_CANDIDATES = ["銘柄コード（Code）", "銘柄コード", "Code", "Ticker", "コード"]
ISIN_COLUMN_CANDIDATES = ["ISINコード", "ISIN"]
NAME_COLUMN_CANDIDATES = ["銘柄（Name）", "銘柄名", "銘柄", "Name"]
SHARES_COLUMN_CANDIDATES = ["株数（※）No. of Shares（※）", "Shares Amount", "No. of Shares", "株数", "Shares"]
PRICE_COLUMN_CANDIDATES = ["Stock Price", "Price", "株価"]
VALUATION_COLUMN_CANDIDATES = ["評価金額(円）Valuation (yen)", "Valuation (yen)", "評価金額", "Valuation"]
WEIGHT_COLUMN_CANDIDATES = ["純資産比率 % of NAV", "純資産比率", "% of NAV", "Weight (%)", "Weight"]
EXCHANGE_COLUMN_CANDIDATES = ["Exchange", "取引所"]
REGION_COLUMN_CANDIDATES = ["Region", "Country", "Location", "国", "地域"]
