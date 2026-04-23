import time
import random
import yfinance as yf
from typing import List, Dict, Optional, Any
from tradingview_ta import get_multiple_analysis, Interval

from helper import get_region
from fields import *

def safe_div(numerator: Optional[float], denominator: Optional[float], default: Any = None) -> Any:
  """Safely handles division and null checks."""
  try:
    if numerator is None or denominator is None or denominator == 0:
      return default
    return numerator / denominator
  except (TypeError, ValueError):
    return default

def get_tv_config():
  """Returns TradingView screener and exchange based on region."""
  return ("japan", "TSE") if get_region() == 'JP' else ("US", "NYSE")

def calculate_price_trends(current: float, history: List[float]):
  """Calculates price change % for 1d, 3d, and 5d intervals."""
  # We need at least 6 points to look back 5 periods (index -6)
  if not current or not history or len(history) < 6:
    return None, None, None

  hp1 = safe_div(current - history[-2], history[-2])
  hp3 = safe_div(current - history[-4], history[-4])
  hp5 = safe_div(current - history[-6], history[-6])
  return hp1, hp3, hp5

def calculate_volume_surges(volume_data: List[int]):
  """Calculates relative volume surges vs historical averages exactly per original logic."""
  if not volume_data or len(volume_data) <= 5:
    return None, None, None

  n = len(volume_data)

  # 1-Day: Today vs everything before today
  base1 = sum(volume_data[:-1]) / (n - 1)
  recent1 = volume_data[-1]
  avg_last_1 = safe_div(recent1 - base1, base1, default=0)

  # 3-Day: Avg of [2 days ago, 1 day ago] vs everything before those 3 days
  base3 = sum(volume_data[:-3]) / (n - 3)
  recent3 = sum(volume_data[-3:-1]) / 2
  avg_last_3 = safe_div(recent3 - base3, base3, default=0)

  # 5-Day: Avg of [4, 3, 2, 1 days ago] vs everything before those 5 days
  base5 = sum(volume_data[:-5]) / (n - 5)
  recent5 = sum(volume_data[-5:-1]) / 4
  avg_last_5 = safe_div(recent5 - base5, base5, default=0)

  return avg_last_1, avg_last_3, avg_last_5

def format_financials(ticker_data: Dict) -> Dict:
  """Maps raw API data to the standardized fields defined in fields.py."""
  curr = ticker_data.get('currentPrice') or ticker_data.get('regularMarketPrice')
  
  # Calculate Targets
  t_high = ticker_data.get('targetHighPrice')
  t_low = ticker_data.get('targetLowPrice')
  t_mean = ticker_data.get('targetMeanPrice')

  # Price & Volume Trends
  vol_1d, vol_3d, vol_5d = calculate_volume_surges(ticker_data.get('volume'))
  hp_1d, hp_3d, hp_5d = calculate_price_trends(curr, ticker_data.get('historical_price'))

  return {
    NAME: ticker_data.get('shortName') or ticker_data.get('longName'),
    MARKET_CAP: ticker_data.get('marketCap'),
    PE_RATIO: ticker_data.get('trailingPE'),
    FORWARD_PE_RATIO: ticker_data.get('forwardPE'),
    PB: ticker_data.get('priceToBook'),
    DIVIDEND_YIELD: safe_div(ticker_data.get('dividendYield'), 100),
    PEG: ticker_data.get('trailingPegRatio'),
    ROA: ticker_data.get('returnOnAssets'),
    ROE: ticker_data.get('returnOnEquity'),
    ROIC: ticker_data.get('returnOnCapital'),
    NET_MARGIN: ticker_data.get('profitMargins'),
    OPERATING_MARGIN: ticker_data.get('operatingMargins'),
    DEBT_TO_EQUITY: safe_div(ticker_data.get('debtToEquity'), 100),
    CURRENT_RATIO: ticker_data.get('currentRatio'),
    TOTAL_CASH_PER_SHARE: ticker_data.get('totalCashPerShare'),
    EARNINGS_GROWTH: ticker_data.get('earningsGrowth'),
    PAYOUT_RATIO: ticker_data.get('payoutRatio'),
    VOL_1D: vol_1d, VOL_3D: vol_3d, VOL_5D: vol_5d,
    PRICE_1D: hp_1d, PRICE_3D: hp_3d, PRICE_5D: hp_5d,
    TARGET_HIGH: t_high,
    TARGET_LOW: t_low,
    TARGET_MEAN: t_mean,
    TARGET_HIGH_PERCENT: safe_div(t_high - curr, curr) if t_high else None,
    TARGET_LOW_PERCENT: safe_div(t_low - curr, curr) if t_low else None,
    TARGET_MEAN_PERCENT: safe_div(t_mean - curr, curr) if t_mean else None,
    CURRENT_PRICE: curr,
    AVG_RATING_1D: ticker_data.get('tv_score_1d', 0),
    AVG_RATING_7D: ticker_data.get('tv_score_1w', 0),
    # AVG_RATING_1M: ticker_data.get('tv_score_1m', 0),
    AVG_RATING: ticker_data.get('averageAnalystRating'),
    SECTOR: ticker_data.get('sector')
  }

def get_tv_scores_batch(tickers: List[str]) -> Dict[str, Dict]:
  """Fetches TV analysis for multiple timeframes and returns a mapped dict."""
  screener, exchange = get_tv_config()
  tv_formatted = [f"{exchange}:{t.replace('.T', '')}" for t in tickers]
  
  intervals = {
    '1d': Interval.INTERVAL_1_DAY,
    '1w': Interval.INTERVAL_1_WEEK,
    # '1m': Interval.INTERVAL_1_MONTH
  }
  
  score_map = {t: {} for t in tv_formatted}

  for key, interval in intervals.items():
    try:
      analysis = get_multiple_analysis(screener=screener, interval=interval, symbols=tv_formatted)
      for sym, data in analysis.items():
        val = data.indicators.get("Recommend.All")
        score_map[sym][key] = round(3 - (val * 2), 2) if val is not None else 0
      time.sleep(random.uniform(1, 2))
    except Exception as e:
      print(f" [!] TV Error ({key}): {e}")
      
  return score_map

def fetch_financials_batch(ticker_list: List[str]) -> List[Dict]:
  """Orchestrates the fetching of YF and TV data for a batch of tickers."""
  results = []
  tv_scores = get_tv_scores_batch(ticker_list)
  _, exchange = get_tv_config()

  for symbol in ticker_list:
    try:
      ticker = yf.Ticker(symbol)
      # Fetch history and info
      info = ticker.info
      hist = ticker.history(period="20d")
      
      info['volume'] = hist['Volume'].tolist()
      info['historical_price'] = hist['Close'].tolist()

      # Map TV Scores
      tv_key = f"{exchange}:{symbol.replace('.T', '')}"
      scores = tv_scores.get(tv_key, {})
      info.update({
        'tv_score_1d': scores.get('1d', 0),
        'tv_score_1w': scores.get('1w', 0),
        # 'tv_score_1m': scores.get('1m', 0)
      })

      results.append(format_financials(info))
      time.sleep(0.1) # Small throttle for YF

    except Exception as e:
      print(f" [!] Error fetching {symbol}: {e}")
      results.append(format_financials({}))

  return results
