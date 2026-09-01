import os
from typing import Optional
from .constants import TV_SLEEP_LOOKUP
from .fields import FAST

def get_region(filename: str) -> str:
  # Extract just the filename in case a full path is passed
  base_name = os.path.basename(filename).upper()

  if 'JP' in base_name:
    return 'JP'
  if 'US' in base_name:
    return 'US'
  if 'HK' in base_name:
    return 'HK'

  return 'Unknown'

def get_tv_sleep_range(ticker_num: int, speed: str) -> tuple[int, int]:
  if speed == FAST:
    return (1, 2)
  for limit, sleep_range in TV_SLEEP_LOOKUP:
    if ticker_num < limit:
      return sleep_range

def prepare_ticker(ticker: str, region: str) -> str:
  """Standardizes ticker format based on region."""
  ticker = str(ticker).strip().lstrip("'")
  if region == 'JP' and not ticker.endswith('.T'):
    return f"{ticker}.T"
  if region == 'HK' and not ticker.endswith('.HK'):
    return f"{ticker}.HK"
  if region == 'US':
    return ticker.replace('.', '-')
  return ticker

def get_tv_screener(region: str) -> str:
  """Returns the TradingView screener name based on region."""
  if region == 'JP':
    return "japan"
  if region == 'HK':
    return "hongkong"
  return "america"

def map_exchange(yf_exchange: str) -> Optional[str]:
  """Maps yfinance exchange codes to TradingView exchange codes.

  Returns None when the exchange is missing or unrecognized, so callers
  can skip the TradingView lookup instead of silently building a wrong
  symbol (e.g. defaulting to TSE for a US/HK ticker).
  """
  if not yf_exchange:
    return None

  # Common yfinance exchange mappings
  mapping = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "AMEX",
    "TSE": "TSE",
    "TYO": "TSE",
    "JPX": "TSE",
    "HKG": "HKEX",
  }
  return mapping.get(yf_exchange)
