import os
from typing import Literal
from constants import FILE_NAME, TV_SLEEP_LOOKUP, SPEED
from fields import FAST

def get_region(filename: str = FILE_NAME) -> str:
  # Extract just the filename in case a full path is passed
  base_name = os.path.basename(filename).upper()

  if 'JP' in base_name:
    return 'JP'
  if 'US' in base_name:
    return 'US'
  if 'HK' in base_name:
    return 'HK'
    
  return 'Unknown'

def get_tv_sleep_range(ticker_num: int) -> tuple[int, int]:
  if SPEED == FAST:
    return (1, 2)
  for limit, sleep_range in TV_SLEEP_LOOKUP:
    if ticker_num < limit:
      return sleep_range

def prepare_ticker(ticker: str) -> str:
  """Standardizes ticker format based on region."""
  ticker = str(ticker).strip().lstrip("'")
  region = get_region()
  if region == 'JP' and not ticker.endswith('.T'):
    return f"{ticker}.T"
  if region == 'HK' and not ticker.endswith('.HK'):
    return f"{ticker}.HK"
  if region == 'US':
    return ticker.replace('.', '-')
  return ticker

def get_tv_screener():
  """Returns the TradingView screener name based on region."""
  region = get_region()
  if region == 'JP':
    return "japan"
  if region == 'HK':
    return "hongkong"
  return "america"

def map_exchange(yf_exchange: str) -> str:
  """Maps yfinance exchange codes to TradingView exchange codes."""
  if not yf_exchange:
    return "TSE"
  
  # Common yfinance exchange mappings
  mapping = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "AMEX",
    "TSE": "TSE",
    "TYO": "TSE",
    "HKG": "HKEX",
  }
  return mapping.get(yf_exchange, "TSE")
