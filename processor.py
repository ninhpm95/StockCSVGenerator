import math
import time
import random
import os
import pandas as pd
from typing import List, Dict
from financials import fetch_financials_batch
from helper import prepare_ticker
from constants import BATCH_SIZE, TV_SLEEP_RANGE, COLUMNS_TO_PRESERVE

def load_and_clean_data(file_path: str) -> pd.DataFrame:
  if not os.path.exists(file_path):
    raise FileNotFoundError(f"File not found: {file_path}")

  df = pd.read_csv(file_path, encoding='utf-8-sig')
  if df.empty:
    raise ValueError("CSV file is empty.")

  existing_cols = [c for c in COLUMNS_TO_PRESERVE if c in df.columns]
  df = df[existing_cols].copy()
  
  ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
  df = df.drop_duplicates(subset=[ticker_col], keep='first')
  
  df['api_ticker'] = df[ticker_col].apply(prepare_ticker)
  return df

def process_batches(tickers: List[str]) -> List[Dict]:
  results = []
  total_batches = math.ceil(len(tickers) / BATCH_SIZE)

  for i in range(0, len(tickers), BATCH_SIZE):
    batch_num = (i // BATCH_SIZE) + 1
    batch = tickers[i : i + BATCH_SIZE]
    print(f"[*] Processing batch {batch_num}/{total_batches}...")
    
    try:
      batch_data = fetch_financials_batch(batch)
      results.extend(batch_data)
    except Exception as e:
      print(f" [!] Batch {batch_num} failed: {e}")
      results.extend([{}] * len(batch))

    if batch_num < total_batches:
      wait = random.uniform(*TV_SLEEP_RANGE)
      print(f"   Cooling down for {wait:.1f}s...")
      time.sleep(wait)
  return results
