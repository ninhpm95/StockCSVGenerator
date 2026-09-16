from pathlib import Path
from .ticker_groups.all_ticker_groups import ALL_TICKER_GROUPS
from .excluded_tickers.all_excluded_tickers import ALL_EXCLUDED_TICKERS

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR.parent.parent / "output"

CURRENT_CSV = OUTPUT_DIR / "JP_ETFs.csv"
DATA_CSV = OUTPUT_DIR / "JP_ETFs_full.csv"

# NOTE: this currently points at the SAME file as DATA_CSV. That makes the
# "found in lookup CSV" check in run.py's group-dedup step (Step 5) weaker
# than it looks -- it's just checking "does this ticker have a parseable Fee
# in the data it already came from," not membership in a separately curated,
# manually-maintained fee list (which is what the surrounding comments in
# run.py describe). Point this at a real separate file once you have one.
LOOKUP_CSV = OUTPUT_DIR / "JP_ETFs_full.csv"  # TODO: point at a real curated fee source
OUTPUT_CSV = OUTPUT_DIR / "screened_etfs.csv"

MIN_AVG_VOLUME = 30_000_000

# Only thresholds >= MIN_AVG_VOLUME can ever match anything: the dataset is
# already filtered down to MIN_AVG_VOLUME before the cascade runs (see Step 1
# in run.py), so lower thresholds are dead weight. If you lower
# MIN_AVG_VOLUME in the future, extend this list to match.
VOLUME_CASCADE = [
    100_000_000, 90_000_000, 80_000_000, 70_000_000,
    60_000_000, 50_000_000, 40_000_000, 30_000_000,
]

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
    AVG_VOLUME = "Avg Volume"
    NOTE = "Note"
