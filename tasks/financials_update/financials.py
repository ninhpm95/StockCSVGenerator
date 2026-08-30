import time
import random
import yfinance as yf
from typing import List, Dict
from tradingview_ta import get_multiple_analysis, Interval

from tasks.financials_update.helper import get_tv_screener, map_exchange
from tasks.financials_update.fields import *
from tasks.financials_update.calculators import safe_div, calculate_price_trends, calculate_volume_surges

def format_financials(ticker_data: Dict) -> Dict:
  curr = ticker_data.get('currentPrice') or ticker_data.get('regularMarketPrice')
  t_high = ticker_data.get('targetHighPrice')
  t_low = ticker_data.get('targetLowPrice')
  t_mean = ticker_data.get('targetMeanPrice')

  vol_1d, vol_3d, vol_5d, vol_30d = calculate_volume_surges(ticker_data.get('volume'))
  hp_1d, hp_3d, hp_5d, hp_7d, hp_10d, hp_15d, hp_20d, hp_30d = calculate_price_trends(curr, ticker_data.get('historical_price'))

  t_high_percent = safe_div(t_high - curr, curr) if t_high else None
  t_low_percent = safe_div(t_low - curr, curr) if t_low else None
  t_mean_percent = safe_div(t_mean - curr, curr) if t_mean else None

  avg_rating_1d = ticker_data.get('tv_score_1d', 0)
  avg_rating_1w = ticker_data.get('tv_score_1w', 0)
  # avg_rating_1m = ticker_data.get('tv_score_1m', 0)

  score = (t_mean_percent or 0) * 100
  multiplier = 10 if score < 0 else 1/10
  if avg_rating_1d >= 3 or avg_rating_1w >= 3:
    score *= multiplier
  if t_low_percent and t_low_percent <= -0.1:
    score *= multiplier
  
  avg_rating = ticker_data.get('averageAnalystRating')
  if avg_rating and ' - ' in avg_rating:
    avg_rating_score, avg_rating_label = avg_rating.split(' - ', 1)
  else:
    avg_rating_score, avg_rating_label = None, None
  
  trailing_pe = ticker_data.get('trailingPE')
  forward_pe = ticker_data.get('forwardPE')

  return {
    NAME: ticker_data.get('longName') or ticker_data.get('shortName'),
    MARKET_CAP: ticker_data.get('marketCap'),
    PE_RATIO: trailing_pe,
    FORWARD_PE_RATIO: forward_pe,
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
    AVG_VOLUME: ticker_data.get('averageVolume10days') * curr if ticker_data.get('averageVolume10days') and curr else None,
    VOL_1D: vol_1d, VOL_3D: vol_3d, VOL_5D: vol_5d, VOL_30D: vol_30d,
    PRICE_1D: hp_1d, PRICE_3D: hp_3d, PRICE_5D: hp_5d, PRICE_7D: hp_7d, PRICE_10D: hp_10d, PRICE_15D: hp_15d, PRICE_20D: hp_20d, PRICE_30D: hp_30d,
    TARGET_HIGH: t_high,
    TARGET_LOW: t_low,
    TARGET_MEAN: t_mean,
    TARGET_HIGH_PERCENT: t_high_percent,
    TARGET_LOW_PERCENT: t_low_percent,
    TARGET_MEAN_PERCENT: t_mean_percent,
    CURRENT_PRICE: curr,
    AVG_RATING_1D: avg_rating_1d if avg_rating_1d != 0 else None,
    AVG_RATING_7D: avg_rating_1w if avg_rating_1w != 0 else None,
    # AVG_RATING_1M: avg_rating_1m,
    # AVG_RATING: ticker_data.get('averageAnalystRating'),
    AVG_RATING_SCORE: avg_rating_score if avg_rating_score != 0 else None,
    AVG_RATING_LABEL: avg_rating_label,
    GROWTH: trailing_pe / forward_pe if isinstance(trailing_pe, (int, float)) and isinstance(forward_pe, (int, float)) else None,
    SECTOR: ticker_data.get('sector')
  }

def get_tv_scores_batch(tv_symbols: List[str], region: str) -> Dict[str, Dict]:
  if not tv_symbols:
    return {}
    
  screener = get_tv_screener(region)
  intervals = {'1d': Interval.INTERVAL_1_DAY, '1w': Interval.INTERVAL_1_WEEK}
  score_map = {sym: {} for sym in tv_symbols}

  for key, interval in intervals.items():
    try:
      analysis = get_multiple_analysis(screener=screener, interval=interval, symbols=tv_symbols)
      
      # SAFE CHECK: ensure analysis itself isn't None
      if analysis:
        for sym, data in analysis.items():
          # SAFE CHECK: ensure the specific symbol's data isn't None
          if data and hasattr(data, 'indicators'):
            val = data.indicators.get("Recommend.All")
            score_map[sym][key] = round(3 - (val * 2), 2) if val is not None else 0
          else:
            score_map[sym][key] = 0
      
      time.sleep(random.uniform(1, 1.5))
    except Exception as e:
      print(f" [!] TV Error ({key}): {e}")
      
  return score_map

def fetch_financials_batch(ticker_list: List[str], region: str, speed: str) -> List[Dict]:
  intermediate_data = []
  tv_symbols_to_fetch = []

  for symbol in ticker_list:
    try:
      yf_sym = symbol
      
      ticker = yf.Ticker(yf_sym)
      
      info = ticker.info
      if not info or ('symbol' not in info and 'shortName' not in info):
        raise ValueError(f"No info returned for {yf_sym}")

      hist = ticker.history(period="60d")
      info['volume'] = hist['Volume'].tolist() if not hist.empty else []
      info['historical_price'] = hist['Close'].tolist() if not hist.empty else []
      
      # Identify Exchange and format for TradingView
      yf_exchange = info.get('exchange')
      mapped_exch = map_exchange(yf_exchange)
      
      # TradingView needs the clean symbol (no .T)
      clean_tv_name = symbol.replace('.T', '')
      tv_sym = f"{mapped_exch}:{clean_tv_name}"
      
      info['tv_key'] = tv_sym
      tv_symbols_to_fetch.append(tv_sym)
      intermediate_data.append(info)
      
      time.sleep(0.05) 
    except Exception as e:
      print(f" [!] Error fetching {symbol}: {e}")
      intermediate_data.append({'shortName': symbol, 'error': True})

  # Step 2: Fetch TV scores
  tv_scores = {} if speed == FAST else get_tv_scores_batch(tv_symbols_to_fetch, region)

  # Step 3: Combine and Format
  final_results = []
  for info in intermediate_data:
    if info.get('error'):
      final_results.append(format_financials(info))
      continue
      
    tv_key = info.get('tv_key')
    scores = tv_scores.get(tv_key, {})
    
    info.update({
      'tv_score_1d': scores.get('1d', 0),
      'tv_score_1w': scores.get('1w', 0),
    })
    final_results.append(format_financials(info))

  return final_results
