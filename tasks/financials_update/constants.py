from . import fields as F

# Paths
OUTPUT_DIR = "output"

# Batch Configurations
# Controls how many tickers are processed together before a cooldown sleep
# (see processor.py). Also doubles as the TradingView `get_multiple_analysis`
# batch size (financials.py); yfinance itself is called per-ticker, not
# batched, so this isn't a "yfinance batch size" - it's the pacing knob for
# both. Kept below common rate-limit thresholds.
BATCH_SIZE = 39

TV_SLEEP_LOOKUP = [
    (100, (25, 40)),
    (300, (30, 45)),
    (600, (35, 50)),
    (float("inf"), (45, 60))
]

# Column Configurations
COLUMNS_TO_PRESERVE = [F.TICKER, F.FEE, F.BOUGHT, F.ISIN, F.NOTE]

FINAL_COLUMNS = [
    F.TICKER, F.NAME,
    F.FEE, F.BOUGHT, F.ISIN,
    F.ROA, F.NET_MARGIN, F.OPERATING_MARGIN, F.DEBT_TO_EQUITY, F.EARNINGS_GROWTH,
    F.VOL_1D, F.VOL_3D, F.VOL_5D, F.VOL_30D,
    F.DIVIDEND_YIELD,
    F.NOTIONAL_VOLUME,
    F.MARKET_CAP,
    F.PE_RATIO, F.FORWARD_PE_RATIO, F.PB,
    F.PRICE_1D, F.PRICE_3D, F.PRICE_5D, F.PRICE_7D, F.PRICE_30D,
    F.TARGET_HIGH_PERCENT, F.TARGET_LOW_PERCENT, F.TARGET_MEAN_PERCENT,
    F.CURRENT_PRICE,
    F.AVG_RATING_1D, F.AVG_RATING_7D, # F.AVG_RATING_1M,
    F.AVG_RATING_SCORE, F.AVG_RATING_LABEL,
    F.GROWTH,
    F.SECTOR,
    F.NOTE
]

# TradingView's 1-month interval score costs an extra API call per batch
# (see get_tv_scores_batch in financials.py) and previously got the script's
# IP rate-limited / blocked by TradingView on large runs. Derived from
# FINAL_COLUMNS rather than a separate hardcoded flag, so there's only one
# place to touch to turn it on: uncomment F.AVG_RATING_1M above. A stray
# `ENABLE_1M_RATING = True` with the column still commented out (or vice
# versa) is no longer possible.
ENABLE_1M_RATING = F.AVG_RATING_1M in FINAL_COLUMNS
