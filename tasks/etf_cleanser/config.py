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
    ("銘柄コード", "ISINコード", "Name", "純資産比率"), # Next Funds
    ("Code", "Name", "ISIN", "Exchange", "Shares Amount", "Stock Price"), # Global X
    ("Code",   "Name", "Weight (%)", "Shares", "Price", "Exchange"), # iShares
    ("Ticker", "Name", "Weight (%)", "Shares", "Price", "Exchange"), # iShares
    ("code", "Name", "Shares Held", "of net asset"), # Maxis
]

# ------------------------------------------------------- column candidates -
# An exact match to a header cell always beats a substring match;
# Otherwise the first candidate that is a substring of a header cell wins
CODE_COLUMN_CANDIDATES = ["Code", "銘柄コード", "Ticker", "code"]
NAME_COLUMN_CANDIDATES = ["Name", "Name(En)"]
ISIN_COLUMN_CANDIDATES = ["ISIN", "ISINコード"]
WEIGHT_COLUMN_CANDIDATES = ["純資産比率", "Weight (%)", "(% of net asset)"]
SHARES_COLUMN_CANDIDATES = ["Shares Amount", "No. of Shares", "Shares", "Shares Held"]
PRICE_COLUMN_CANDIDATES = ["Stock Price", "Price"]
VALUATION_COLUMN_CANDIDATES = ["評価金額", "Notional Value", "Market Value/Jpy"]
EXCHANGE_COLUMN_CANDIDATES = ["Exchange"]

# Order in which fields claim header columns. A header cell can belong to only
# one field, so an earlier field wins if two fields' candidates both match the
# same cell. Fields left out of this list are never resolved.
COLUMN_RESOLUTION_ORDER = ["code", "name", "isin", "shares", "price", "valuation", "weight", "exchange"]

# Step 082 removes a row if its Name contains any of these texts (case
# sensitive substring), treating it as if both Code and Name were empty.
# Use it for Total / footnote lines. Append more as you find them,
# e.g. ["Total Net Assets", "合計"].
SKIP_NAME_TEXTS = ["Total Net Assets", "合計"]

# ------------------------------------------------- output checks (step 100) --
# Log only - the checks never change anything.
CHECK_DOMINANT_SHARE = 0.90        # "most" = at least this share of the rows
HOLDINGS_MIN_COUNT = 10            # ticker and ISIN checks only run with at least this many values
EXCHANGE_SHORT_MAX_LEN = 10        # longer than this = not a short form (TSE, NYSE, Nasdaq)
WEIGHT_SUM_MAX = 100.0             # weights are in percent; a total above this is flagged
WEIGHT_SUM_MIN = 60.0              # a total below this is flagged (holdings probably incomplete)
CHECK_MAX_LISTED = 3               # rows printed per finding; the rest are counted


@dataclass
class Config:
    input_dir: Path = DATA_DIR
    output_dir: Path = OUTPUT_DIR
    lookup_path: Path = LOOKUP_PATH
    verbose: bool = False          # print a traceback when a step fails
