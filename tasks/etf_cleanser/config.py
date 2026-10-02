"""Central configuration: paths, file-name rules, keyword tuples, column
candidates, check thresholds."""
from dataclasses import dataclass
from pathlib import Path

# ------------------------------------------------------------------ paths --
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent

DATA_DIR = PROJECT_DIR / "data" / "ETFs"
OUTPUT_DIR = PROJECT_DIR / "data" / "cleansed_ETFs"
LOOKUP_PATH = PROJECT_DIR / "data" / "stocks" / "GLOBAL_lookup.csv"

# ---------------------------------------------------------- input reading --
# Excel workbooks: only one sheet holds data; every other sheet is ignored.
# Names are tried left to right and the first one found wins (the rest are
# ignored). If none is found the file fails. To support another provider,
# append its sheet name, e.g. ["保有明細", "Holding sheet"].
HOLDINGS_SHEET = ["保有明細"]

# If a file contains this text, the LAST row containing it starts the real
# ETF holdings: everything above that row is dropped (step 020).
HOLDINGS_MARK = "Fund Holdings as of"        # case sensitive, matched anywhere in a cell

# ------------------------------------------------------- file-name rules ---
# What an ETF ticker looks like in a file name, e.g. "159A", "1306", "SPY".
_TICKER = r"[0-9A-Z]{3,5}"

# Step 070 (Ticker refresh) only touches files named "<Ticker>.csv"
# (or .xlsx ...). Matched against the whole file stem.
TICKER_FILENAME_PATTERN = _TICKER

# ------------------------------------------------------------------ output --
# Same columns for every file (order = order written by step 090).
OUTPUT_COLUMNS = ["Ticker", "Name", "ISIN", "Weight(%)", "Exchange"]

# ------------------------------------------------------ header detection ---
# First row matching any tuple (cells left-to-right, tuples in list order).
HEADER_KEYWORD_COMBINATIONS = [
    ("銘柄コード", "ISINコード", "Name"),
    ("Code", "Name", "ISIN"),
    ("Code", "Name", "Weight"),
    ("Ticker", "Name", "Weight"),
    ("code", "Name", "Shares Held"),      # Maxis
]

# ------------------------------------------------------- column candidates -
# Tried top to bottom; first candidate equal (after normalisation) to a
# header cell wins. Order = priority. Matching is CASE SENSITIVE, so list
# each spelling you need ("Code" and "code" are different candidates).
# Normalisation only unifies full/half-width brackets, % and spaces, and
# removes whitespace - it does not change case.
CODE_COLUMN_CANDIDATES = ["銘柄コード（Code）", "銘柄コード", "Code", "code", "Ticker", "コード"]   # "code" = Maxis
ISIN_COLUMN_CANDIDATES = ["ISINコード", "ISIN"]
NAME_COLUMN_CANDIDATES = ["銘柄（Name）", "銘柄名", "銘柄", "Name"]
SHARES_COLUMN_CANDIDATES = ["株数（※）No. of Shares（※）", "Shares Amount", "No. of Shares", "株数", "Shares", "Shares Held"]
PRICE_COLUMN_CANDIDATES = ["Stock Price", "Price", "Market Value", "株価"]
VALUATION_COLUMN_CANDIDATES = ["評価金額(円）Valuation (yen)", "Valuation (yen)", "評価金額", "Valuation"]
WEIGHT_COLUMN_CANDIDATES = ["純資産比率 % of NAV", "純資産比率", "% of NAV", "Weight (%)", "Weight", "% of net asset"]
EXCHANGE_COLUMN_CANDIDATES = ["Exchange", "取引所"]

# Order in which fields claim header columns. A header cell can belong to only
# one field, so an earlier field wins if two fields' candidates both match the
# same cell. Fields left out of this list are never resolved.
COLUMN_RESOLUTION_ORDER = ["code", "name", "isin", "shares", "price",
                           "valuation", "weight", "exchange"]

# ------------------------------------------------- output checks (step 100) --
# Log only - the checks never change anything.
CHECK_DOMINANT_SHARE = 0.90        # "most" = at least this share of the rows
HOLDINGS_MIN_COUNT = 10              # ticker and ISIN checks only run with at least this many values
EXCHANGE_SHORT_MAX_LEN = 10        # longer than this = not a short form (TSE, NYSE, Nasdaq)
WEIGHT_SUM_MAX = 100.0             # weights are in percent; a total above this is flagged
WEIGHT_SUM_MIN = 60.0              # a total below this is flagged (holdings probably incomplete)
CHECK_MAX_LISTED = 20              # rows printed per finding; the rest are counted


@dataclass
class Config:
    input_dir: Path = DATA_DIR
    output_dir: Path = OUTPUT_DIR
    lookup_path: Path = LOOKUP_PATH
    verbose: bool = False          # print a traceback when a step fails
