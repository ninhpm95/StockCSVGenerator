from . import fields as F

# Paths
OUTPUT_DIR = "output"

# Batch Configurations
BATCH_SIZE = 39  # yfinance/TradingView batch fetch size - kept below common rate-limit thresholds

# TradingView's 1-month interval score. Off by default - a large batch of
# lookups against this interval previously got the script's IP rate-limited
# / blocked by TradingView. Safe to flip on for a small stock list; leave
# off for full-size runs.
ENABLE_1M_RATING = False

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
