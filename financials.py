import time
import yfinance as yf
from tradingview_ta import get_multiple_analysis, Interval
from helper import get_region
from fields import *

def calculate_price_trends(current_price, historical_price):
  if not historical_price or len(historical_price) <= 5 or not current_price:
    return None, None, None
  
  # Basic safety: ensure we have enough data points
  try:
    hp1 = (current_price - historical_price[-2]) / historical_price[-2]
    hp3 = (current_price - historical_price[-4]) / historical_price[-4]
    hp5 = (current_price - historical_price[-6]) / historical_price[-6]
    return hp1, hp3, hp5
  except (IndexError, ZeroDivisionError):
    return None, None, None

def calculate_volume_surges(volume_data):
  if not volume_data or len(volume_data) <= 5:
    return None, None, None

  n = len(volume_data)
  base1 = sum(volume_data[:-1]) / (n - 1)
  base3 = sum(volume_data[:-3]) / (n - 3)
  base5 = sum(volume_data[:-5]) / (n - 5)

  recent1 = volume_data[-1]
  recent3 = sum(volume_data[-3:-1]) / 2
  recent5 = sum(volume_data[-5:-1]) / 4

  avg_last_1 = (recent1 - base1) / base1 if base1 else 0
  avg_last_3 = (recent3 - base3) / base3 if base3 else 0
  avg_last_5 = (recent5 - base5) / base5 if base5 else 0

  return avg_last_1, avg_last_3, avg_last_5

def format_financials(ticker_data):
  current_price = ticker_data.get('currentPrice') or ticker_data.get('regularMarketPrice')

  target_high = ticker_data.get('targetHighPrice')
  target_high_percent = (target_high - current_price) / current_price if target_high and current_price else None
  target_low = ticker_data.get('targetLowPrice')
  target_low_percent = (target_low - current_price) / current_price if target_low and current_price else None
  target_mean = ticker_data.get('targetMeanPrice')
  target_mean_percent = (target_mean - current_price) / current_price if target_mean and current_price else None

  volume = ticker_data.get('volume')
  vol_1d, vol_3d, vol_5d = calculate_volume_surges(volume)

  historical_price = ticker_data.get('historical_price')
  hp_1d, hp_3d, hp_5d = calculate_price_trends(current_price, historical_price)

  short_term_score_1d = ticker_data.get('tv_score_1d', 0)
  short_term_score_7d = ticker_data.get('tv_score_1w', 0)

  data = {
    NAME: ticker_data.get('shortName') or ticker_data.get('longName'),
    MARKET_CAP: ticker_data.get('marketCap'),
    PE_RATIO: ticker_data.get('trailingPE'),
    FORWARD_PE_RATIO: ticker_data.get('forwardPE'),
    PB: ticker_data.get('priceToBook'),
    DIVIDEND_YIELD: ticker_data.get('dividendYield') / 100 if ticker_data.get('dividendYield') else None,

    PEG: ticker_data.get('trailingPegRatio'),
    ROA: ticker_data.get('returnOnAssets'), ROE: ticker_data.get('returnOnEquity'), ROIC: ticker_data.get('returnOnCapital'), 
    NET_MARGIN: ticker_data.get('profitMargins'), OPERATING_MARGIN: ticker_data.get('operatingMargins'),
    DEBT_TO_EQUITY: ticker_data.get('debtToEquity') / 100 if ticker_data.get('debtToEquity') else None,
    CURRENT_RATIO: ticker_data.get('currentRatio'),
    TOTAL_CASH_PER_SHARE: ticker_data.get('totalCashPerShare'),
    EARNINGS_GROWTH: ticker_data.get('earningsGrowth'),
    PAYOUT_RATIO: ticker_data.get('payoutRatio'),

    VOL_1D: vol_1d, VOL_3D: vol_3d, VOL_5D: vol_5d,
    PRICE_1D: hp_1d, PRICE_3D: hp_3d, PRICE_5D: hp_5d,

    TARGET_HIGH: target_high, TARGET_LOW: target_low, TARGET_MEAN: target_mean,
    TARGET_HIGH_PERCENT: target_high_percent, TARGET_LOW_PERCENT: target_low_percent, TARGET_MEAN_PERCENT: target_mean_percent,
    CURRENT_PRICE: current_price,
    AVG_RATING_1D: short_term_score_1d, AVG_RATING_7D: short_term_score_7d, AVG_RATING: ticker_data.get('averageAnalystRating'),
    SECTOR: ticker_data.get('sector')
  }
  return data

def get_batch_analysis(tickers, period):
  if get_region() == 'JP':
    screener, exchange = "japan", "TSE"
  else:
    screener, exchange = "US", "NYSE"

  tv_tickers = [t.replace('.T', '') for t in tickers]
  tv_formatted = [f"{exchange}:{t}" for t in tv_tickers]

  try:
    return get_multiple_analysis(screener=screener, interval=period, symbols=tv_formatted)
  except Exception as e:
    print(f" [!] TV Batch Error: {e}")
    return {}

def fetch_financials(ticker_symbol, tv_scores=None):
  try:
    ticker = yf.Ticker(ticker_symbol)
    ticker_data = ticker.info
    history = ticker.history(period="20d")
    volume = history['Volume'].tolist()
    historical_price = history['Close'].tolist()
    ticker_data['volume'] = volume
    ticker_data['historical_price'] = historical_price

    if tv_scores:
      ticker_data['tv_score_1d'] = tv_scores.get('1d', 0)
      ticker_data['tv_score_1w'] = tv_scores.get('1w', 0)
    
    return format_financials(ticker_data)

  except Exception as e:
    print(f" [!] Error fetching {ticker_symbol}: {e}")
    return format_financials({})

def fetch_financials_batch(ticker_list):
  results = []
  tv_1d = get_batch_analysis(ticker_list, Interval.INTERVAL_1_DAY)
  tv_1w = get_batch_analysis(ticker_list, Interval.INTERVAL_1_WEEK)

  for symbol in ticker_list:
    # Match the key format used in get_batch_analysis
    exchange = "TSE" if get_region() == 'JP' else "NYSE"
    tv_key = f"{exchange}:{symbol.replace('.T', '')}"
    
    def extract_score(batch_res):
      analysis = batch_res.get(tv_key)
      if analysis and "Recommend.All" in analysis.indicators:
        return round(3 - (analysis.indicators["Recommend.All"] * 2), 2)
      return 0

    scores = {'1d': extract_score(tv_1d), '1w': extract_score(tv_1w)}
    results.append(fetch_financials(symbol, tv_scores=scores))
    time.sleep(0.2)
            
  return results
