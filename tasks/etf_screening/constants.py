from pathlib import Path
from .ticker_groups.all_ticker_groups import ALL_TICKER_GROUPS
from .excluded_tickers.all_excluded_tickers import ALL_EXCLUDED_TICKERS

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR.parent.parent / "output"

CURRENT_CSV = OUTPUT_DIR / "JP_ETFs.csv"
DATA_CSV = OUTPUT_DIR / "JP_ETFs_full.csv"

# Used only for fee comparisons; output Fee text always comes from DATA_CSV.
LOOKUP_CSV = DATA_CSV
# Read existing annotations first, then replace the current list with results.
OUTPUT_CSV = CURRENT_CSV

# CSV read/write encoding. JP-sourced spreadsheet exports (Excel, etc.) are
# sometimes Shift-JIS/cp932 rather than utf-8 -- if you hit UnicodeDecodeError
# on a real export, change this rather than hardcoding encoding= in run.py.
CSV_ENCODING = "utf-8"

# Literal website footnote text to remove from the output Fee column.
FEE_TEXT_TO_REMOVE = ["(*3)"]

MIN_AVG_VOLUME = 30_000_000

# Only thresholds >= MIN_AVG_VOLUME can ever match anything: the dataset is
# already filtered down to MIN_AVG_VOLUME before the cascade runs (see Step 1
# in run.py), so lower thresholds are dead weight. MIN_AVG_VOLUME is folded
# in automatically (via the sorted(set(...) | {...}) below) so this list and
# MIN_AVG_VOLUME can't silently drift out of sync, and the sort guarantees
# descending order, which the cascade loop in run.py depends on.
VOLUME_CASCADE = sorted(
    {
        100_000_000, 90_000_000, 80_000_000, 70_000_000,
        60_000_000, 50_000_000, 40_000_000, 30_000_000,
        MIN_AVG_VOLUME,
    },
    reverse=True,
)

TICKER_GROUPS = ALL_TICKER_GROUPS
EXCLUDED_TICKERS = ALL_EXCLUDED_TICKERS


class Fields:
    """Centralized DataFrame column-name constants.

    Import this instead of scattering string literals ("Ticker", "Fee", ...)
    across the codebase -- gives one place to check/rename schema columns and
    protects against silent typos.
    """
    TICKER = "Ticker"
    NAME = "Name"
    FEE = "Fee"
    TER = "TER"
    BOUGHT = "Bought"
    NOTIONAL_VOLUME = "Notional Volume"
    NOTE = "Note"
