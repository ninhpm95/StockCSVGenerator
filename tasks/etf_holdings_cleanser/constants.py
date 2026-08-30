import re
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_ROOT.parent.parent / "data" / "ETFs"
OUTPUT_DIR = PROJECT_ROOT / "cleansed_ETFs"

# Reference data files.
STOCK_LIST_CSV = PROJECT_ROOT.parent.parent / "data" / "stock_list.csv"
ETF_LIST_CSV = PROJECT_ROOT.parent.parent / "data" / "etf_list.csv"

XLSX_SHEET_NAMES = ["保有明細"]
TRUNCATE_MARKER = "fund holdings as of"  # case-insensitive substring match
HEADER_SCAN_LIMIT = 50  # how many rows (from top) to scan for a header row

# A row is treated as the header row if, for ANY of the combinations below,
# each of the 3 keywords is found (as a case-insensitive substring) in a
# distinct cell within that row, AND the keywords appear in that same
# left-to-right order across the row (columns need not be adjacent, but
# e.g. "isin" must not appear to the left of "code").
HEADER_KEYWORD_COMBINATIONS = [
    ("銘柄コード", "ISINコード", "Name"),
    ("Code", "Name", "ISIN"),
    ("Code", "Name", "Weight"),
    ("Ticker", "Name", "Weight"),
]

CODE_COLUMN_KEYWORDS = ["銘柄コード", "code", "ticker"]
NAME_COLUMN_KEYWORDS = ["name"]
ISIN_COLUMN_KEYWORDS = ["isin"]

SKIP_KEYWORDS = [
    '合計', '総額', 'Total', 'NET ASSETS', '純資産',
    'CASH', '現金', '預金', 'FUTURES', '先物', 'MICRO EMIN'
]

# Futures/forward contract names typically end in a month abbreviation +
# 2-4 digit year (e.g. "TOPIX FUTURES SEP.2026", "SP500 MIC EMIN FUTSep26",
# "DJIA MICR MIN CBOTSep26"). These legitimately have no ISIN and will
# never be in the stock list, so they shouldn't be logged as unmatched.
FUTURES_DATE_PATTERN = re.compile(
    r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s*\d{2,4}$",
    re.IGNORECASE,
)

# Standard ISIN shape: 2-letter country code + 9 alphanumeric chars + 1
# check digit (e.g. "INE002A01018"). Used to tell apart:
#   - a real, identifiable security that simply isn't in stock_list.csv
#     yet (actionable - the reference data needs updating)
#   - a holding with no ISIN at all, which is normal for bonds, T-bills,
#     and similar non-equity instruments (not actionable, expected)
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
