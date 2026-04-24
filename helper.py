import os
from typing import Literal
from constants import FILE_NAME

RegionType = Literal['JP', 'US', 'Unknown']

def get_region(filename: str = FILE_NAME) -> RegionType:
  """
  Determines the market region based on the filename.
  
  Args:
    filename: The name or path of the file (defaults to FILE_NAME).
    
  Returns:
    'JP', 'US', or 'Unknown'
  }
  """
  # Extract just the filename in case a full path is passed
  base_name = os.path.basename(filename).upper()

  if 'JP' in base_name:
    return 'JP'
  if 'US' in base_name:
    return 'US'
    
  return 'Unknown'

def prepare_ticker(ticker: str) -> str:
  """Standardizes ticker format based on region."""
  ticker = str(ticker).strip()
  if ticker.endswith('.T') or get_region() != 'JP':
    return ticker
  return f"{ticker}.T"

def get_tv_screener():
  """Returns the TradingView screener name based on region."""
  return "japan" if get_region() == 'JP' else "america"

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
    "TYO": "TSE"
  }
  return mapping.get(yf_exchange, "TSE")
