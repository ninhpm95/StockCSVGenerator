"""Central configuration: paths, optional step settings, file-name rules, keyword tuples, column
candidates, check thresholds."""
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------------ paths --
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent.parent

DATA_DIR = PROJECT_DIR / "data" / "ETFs"
OUTPUT_DIR = PROJECT_DIR / "data" / "cleansed_ETFs"
LOOKUP_PATH = PROJECT_DIR / "data" / "stocks" / "GLOBAL_lookup.csv"

EXCHANGES_PATH = PROJECT_DIR / "data" / "exchanges.csv"

# -------------------------------------------------------- optional steps --
# Set a switch to False to skip that step for every input file.
ENABLE_MANUAL_UPDATES = True
# Exchange refresh only fills missing values; existing values are retained.
ENABLE_REFRESH_EXCHANGES = True
ENABLE_UNIFY_EXCHANGE_NAMES = True
ENABLE_REFRESH_TICKERS = True
# True applies ticker refresh to all supported holdings files, regardless of name/type.
# ENABLE_REFRESH_TICKERS remains the master switch for the step.
REFRESH_TICKERS_ALL_FILES = False
# ISIN refresh only fills missing values; existing values are retained.
ENABLE_REFRESH_ISIN = True
ENABLE_CALC_WEIGHTS = True
ENABLE_SORT_BY_WEIGHT = True
ENABLE_CHECK = True

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

# Default step 070 scope: <Ticker>.csv files, unless REFRESH_TICKERS_ALL_FILES is True.
# Matched against the whole file stem.
TICKER_FILENAME_PATTERN = _TICKER

# ------------------------------------------------------------------ output --
# Same columns for every file (order = order written by step 120).
OUTPUT_COLUMNS = ["Ticker", "Name", "ISIN", "Weight(%)", "Exchange"]

# --------------------------------------------------------- manual updates --
# All conditions must match. A string is shorthand for {"value": string};
# caseSensitive and fullMatch both default to True. Rules run in list order.
MANUAL_UPDATE_RULES = [
    {
        "code": "700",
        # "isin": "KYG875721634",
        "name": {"value": "TENCENT HOLDINGS", "caseSensitive": True, "fullMatch": False},
        "TO_BE_CHANGED": {"code": "80700"},
    },
]

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
CURRENCY_COLUMN_CANDIDATES = ["Currency"]

# Order in which fields claim header columns. A header cell can belong to only
# one field, so an earlier field wins if two fields' candidates both match the
# same cell. Fields left out of this list are never resolved.
COLUMN_RESOLUTION_ORDER = ["code", "name", "isin", "shares", "price", "valuation", "weight", "exchange", "currency"]

# Step 070 (either file scope): a row matching ANY rule keeps its Ticker.
# Keys are Row field names, values are compared case-insensitively.
EXCLUDE_RULES = {"exchange": "TSE", "currency": "JPY"}
WEIGHT_TOTAL_EXCLUDE_NAMES = ["FWRDUSDJPY01", "FWRDEURJPY01"]

# Step 090 removes a row if its Name contains any of these texts (case
# sensitive substring), treating it as if both Code and Name were empty.
# Use it for Total / footnote lines. Append more as you find them,
# e.g. ["Total Net Assets", "合計"].
SKIP_NAME_TEXTS = ["Total", "合計", "計"]
CODE_TO_CHECK_NAME_TO_SKIP = [""]

# ------------------------------------------------- output checks (step 130) --
# Log only - the checks never change anything.
EXCHANGE_UNKNOWN_MAX_LEN = 10      # flag longer names only when not a canonical exchanges.csv target
WEIGHT_SUM_MAX = 101               # weights are in percent; a total above this is flagged
WEIGHT_SUM_MIN = 60.0              # a total below this is flagged (holdings probably incomplete)
CHECK_MAX_LISTED = 3               # rows printed per finding; the rest are counted

# Files whose weights legitimately don't add up to ~100% (e.g. 2x leveraged
# funds that include futures/swap notional). Key = file stem (no extension),
# value = expected total in percent. A total within +/- the tolerance is fine.
# Verify each fund's structure before listing it.
WEIGHT_SUM_EXPECTED = {
    # "1357_brd_data": 200,
    # "1472_brd_data": 200,
    # "1570_brd_data": 200,
    # "2251_brd_data": 200,
}
WEIGHT_SUM_EXPECTED_TOLERANCE = 5


@dataclass
class Config:
    input_dir: Path = DATA_DIR
    output_dir: Path = OUTPUT_DIR
    lookup_path: Path = LOOKUP_PATH
    exchanges_path: Path = EXCHANGES_PATH
    verbose: bool = False          # print a traceback when a step fails
    enable_manual_updates: bool = ENABLE_MANUAL_UPDATES
    manual_update_rules: list[dict] = field(default_factory=lambda: list(MANUAL_UPDATE_RULES))
    enable_refresh_exchanges: bool = ENABLE_REFRESH_EXCHANGES
    enable_unify_exchange_names: bool = ENABLE_UNIFY_EXCHANGE_NAMES
    enable_refresh_tickers: bool = ENABLE_REFRESH_TICKERS
    refresh_tickers_all_files: bool = REFRESH_TICKERS_ALL_FILES
    enable_refresh_isin: bool = ENABLE_REFRESH_ISIN
    enable_calc_weights: bool = ENABLE_CALC_WEIGHTS
    enable_sort_by_weight: bool = ENABLE_SORT_BY_WEIGHT
    enable_check: bool = ENABLE_CHECK
