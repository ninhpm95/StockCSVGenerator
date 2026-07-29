import os
from .ticker_groups.all_ticker_groups import ALL_TICKER_GROUPS
from .excluded_tickers.all_excluded_tickers import ALL_EXCLUDED_TICKERS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_CSV = os.path.join(BASE_DIR, "..", "output", "JP_ETFs_full.csv")
LOOKUP_CSV = os.path.join(BASE_DIR, "..", "output", "Old", "JP_ETFs_full_old.csv")
OUTPUT_CSV = os.path.join(BASE_DIR, "..", "output", "filtered_etfs.csv")

MIN_AVG_VOLUME = 50_000_000
VOLUME_CASCADE = [80_000_000, 70_000_000, 60_000_000, 50_000_000, 40_000_000, 30_000_000, 20_000_000, 10_000_000, 0]

TICKER_GROUPS = ALL_TICKER_GROUPS
EXCLUDED_TICKERS = ALL_EXCLUDED_TICKERS

TICKER = "Ticker"
NAME = "Name"
AVG_VOLUME = "Avg Volume"
