import time
import random
import yfinance as yf
from typing import List, Dict
from tradingview_ta import get_multiple_analysis, Interval

from helper import get_region
from fields import *
from calculators import safe_div, calculate_price_trends, calculate_volume_surges

def get_tv_config():
  return ("japan", "TSE") if get_region() == 'JP' else ("US", "NYSE")

def format_financials(ticker_data: Dict) -> Dict:
  curr = ticker_data.get('currentPrice') or ticker_data.get('regularMarketPrice')
  t_high = ticker_data.get('targetHighPrice')
  t_low = ticker_data.get('targetLowPrice')
  t_mean = ticker_data.get('targetMeanPrice')

  vol_1d, vol_3d, vol_5d = calculate_volume_surges(ticker_data.get('volume'))
  hp_1d, hp_3d, hp_5d = calculate_price_trends(curr, ticker_data.get('historical_price'))

  t_high_percent = safe_div(t_high - curr, curr) if t_high else None
  t_low_percent = safe_div(t_low - curr, curr) if t_low else None
  t_mean_percent = safe_div(t_mean - curr, curr) if t_mean else None

  avg_rating_1d = ticker_data.get('tv_score_1d', 0)
  avg_rating_1w = ticker_data.get('tv_score_1w', 0)
  # avg_rating_1m = ticker_data.get('tv_score_1m', 0)

  score = t_mean_percent * 100
  multiplier = 10 if score < 0 else 1/10
  if avg_rating_1d >= 3 or avg_rating_1w >= 3:
    score *= multiplier
  if t_low_percent <= -0.1:
    score *= multiplier
  
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
    TARGET_HIGH_PERCENT: t_high_percent,
    TARGET_LOW_PERCENT: t_low_percent,
    TARGET_MEAN_PERCENT: t_mean_percent,
    CURRENT_PRICE: curr,
    AVG_RATING_1D: avg_rating_1d,
    AVG_RATING_7D: avg_rating_1w,
    # AVG_RATING_1M: avg_rating_1m,
    AVG_RATING: ticker_data.get('averageAnalystRating'),
    SCORE: score,
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
