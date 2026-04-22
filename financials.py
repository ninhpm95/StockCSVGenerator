import yfinance as yf
from tradingview_ta import TA_Handler, Interval
from helper import get_region
from fields import *

def calculate_price_trends(current_price, historical_price):
    if not historical_price or len(historical_price) <= 5:
        return None, None, None

    # --- PRICE JUMPS (The "Direction") ---
    # We compare current price vs the price at the start of the window
    hp1 = (current_price - historical_price[-2]) / historical_price[-2] # 1 day  ago
    hp3 = (current_price - historical_price[-4]) / historical_price[-4] # 3 days ago
    hp5 = (current_price - historical_price[-6]) / historical_price[-6] # 5 days ago

    return hp1, hp3, hp5

def calculate_volume_surges(volume_data):
    """
    Calculates the relative volume surge for the last 1, 3, and 5 days
    compared to their respective historical baselines.
    """
    # Ensure volume_data exists and has enough entries
    if not volume_data or len(volume_data) <= 5:
        return None, None, None

    n = len(volume_data)

    # --- BASELINES (Historical volume excluding the recent window) ---
    base1 = sum(volume_data[:-1]) / (n - 1)
    base3 = sum(volume_data[:-3]) / (n - 3)
    base5 = sum(volume_data[:-5]) / (n - 5)

    # --- RECENT AVERAGES (The windows we are testing) ---
    recent1 = volume_data[-1]
    recent3 = sum(volume_data[-3:-1]) / 2
    recent5 = sum(volume_data[-5:-1]) / 4

    # --- RELATIVE CALCULATIONS ---
    # Result is a decimal (e.g., 0.5 means 50% above baseline)
    avg_last_1 = (recent1 - base1) / base1 if base1 else 0
    avg_last_3 = (recent3 - base3) / base3 if base3 else 0
    avg_last_5 = (recent5 - base5) / base5 if base5 else 0

    return avg_last_1, avg_last_3, avg_last_5

def get_short_term_analysis(symbol, period):
    return 0
    # if get_region()== 'JP':
    #     screener = "japan"
    #     exchange = "TSE"
    # else:
    #     screener = "US"
    #     exchange = "NYSE"

    # # Initialize handler
    # handler = TA_Handler(
    #     symbol=symbol.replace('.T', ''),
    #     screener=screener,
    #     exchange=exchange,
    #     interval=period
    # )

    # analysis = handler.get_analysis()

    # # Get the raw TradingView score (-1 to 1)
    # raw_score = analysis.indicators["Recommend.All"]

    # # Convert to a 1-5 scale (1 = Strong Buy, 5 = Strong Sell)
    # # Formula: 3 - (raw_score * 2)
    # normalized_score = round(3 - (raw_score * 2), 2)
    # return normalized_score

def get_data(ticker_symbol,info):
  current_price = info.get('currentPrice') or info.get('regularMarketPrice')

  target_high = info.get('targetHighPrice')
  target_high_percent = (target_high - current_price) / current_price if target_high and current_price else None
  target_low = info.get('targetLowPrice')
  target_low_percent = (target_low - current_price) / current_price if target_low and current_price else None
  target_mean = info.get('targetMeanPrice')
  target_mean_percent = (target_mean - current_price) / current_price if target_mean and current_price else None

  volume = info.get('volume')
  vol_1d, vol_3d, vol_5d = calculate_volume_surges(volume)

  historical_price = info.get('historical_price')
  hp_1d, hp_3d, hp_5d = calculate_price_trends(current_price, historical_price)

  short_term_score_1d = get_short_term_analysis(ticker_symbol, Interval.INTERVAL_1_DAY)
  short_term_score_7d = get_short_term_analysis(ticker_symbol, Interval.INTERVAL_1_WEEK)

  data = {
    NAME: info.get('shortName') or info.get('longName'),
    MARKET_CAP: info.get('marketCap'),
    PE_RATIO: info.get('trailingPE'),
    FORWARD_PE_RATIO: info.get('forwardPE'),
    PB: info.get('priceToBook'),
    DIVIDEND_YIELD: info.get('dividendYield') / 100 if info.get('dividendYield') else None,

    PEG: info.get('trailingPegRatio'),
    ROA: info.get('returnOnAssets'),
    ROE: info.get('returnOnEquity'),
    ROIC: info.get('returnOnCapital'), 
    NET_MARGIN: info.get('profitMargins'),
    OPERATING_MARGIN: info.get('operatingMargins'),
    DEBT_TO_EQUITY: info.get('debtToEquity') / 100 if info.get('debtToEquity') else None,
    CURRENT_RATIO: info.get('currentRatio'),
    TOTAL_CASH_PER_SHARE: info.get('totalCashPerShare'),
    EARNINGS_GROWTH: info.get('earningsGrowth'),
    PAYOUT_RATIO: info.get('payoutRatio'),

    PRICE_1D: hp_1d,
    PRICE_3D: hp_3d,
    PRICE_5D: hp_5d,

    VOL_1D: vol_1d,
    VOL_3D: vol_3d,
    VOL_5D: vol_5d,

    TARGET_HIGH: target_high,
    TARGET_LOW: target_low,
    TARGET_MEAN: target_mean,
    TARGET_HIGH_PERCENT: target_high_percent,
    TARGET_LOW_PERCENT: target_low_percent,
    TARGET_MEAN_PERCENT: target_mean_percent,
    CURRENT_PRICE: current_price,

    SECTOR: info.get('sector'),
    AVG_RATING: info.get('averageAnalystRating'),
    AVG_RATING_1D: short_term_score_1d,
    AVG_RATING_7D: short_term_score_7d
  }
  return data

def fetch_financials(ticker_symbol):
  try:
    ticker = yf.Ticker(ticker_symbol)
    info = ticker.info
    history = ticker.history(period="20d")
    volume = history['Volume'].tolist()
    historical_price = history['Close'].tolist()
    info['volume'] = volume
    info['historical_price'] = historical_price
    return get_data(ticker_symbol, info)

  except Exception as e:
    print(f" [!] Error fetching {ticker_symbol}: {e}")
    return get_data(ticker_symbol,{})
