from pathlib import Path

from .fields import Fields as F

# --------------------------------------------------------------------------
# Input/output locations.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent           # parent/tasks/ticker_extractor
REPO_ROOT = PROJECT_ROOT.parent.parent                    # parent
HOLDINGS_DIR = REPO_ROOT / "data" / "cleansed_ETFs"
EXCHANGES_PATH = REPO_ROOT / "data" / "exchanges.csv"
OUTPUT_DIR = PROJECT_ROOT / "output"

# False: all holdings. True: largest holdings covering TOP_WEIGHT_PERCENT of each ETF.
FILTER_BY_CUMULATIVE_WEIGHT = False
TOP_WEIGHT_PERCENT = 80.0
# Files with fewer holdings skip cumulative-weight selection. Set to 0 to disable.
MIN_HOLDINGS_FOR_WEIGHT_FILTER = 300

# Extract only these fields; missing source columns are filled with blanks.
OUTPUT_FIELDS = [F.TICKER, F.NAME, F.ISIN, F.EXCHANGE]


# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------

# Tokens that source files use to mean "no value" rather than leaving the
# cell blank. These normalize to "" so downstream logic isn't fooled into
# treating a placeholder dash as a real value.
#
# NOTE: bare "na" is deliberately NOT included here even though "n/a" is --
# "na" collides with real-world tickers/names (e.g. National Bank of
# Canada trades as "NA"), so treating it as a placeholder would silently
# drop legitimate holdings. "n/a" (with the slash) is unambiguous.
PLACEHOLDER_TOKENS = {"-", "--", "―", "‐", "‑", "n/a", "null", "none"}


# Country grouping comes exclusively from exchanges.csv.
UNKNOWN_REGION = "UNKNOWN"
