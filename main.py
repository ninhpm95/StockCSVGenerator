import os
import time
import random
import math
import pandas as pd
from typing import List, Dict

from helper import get_region
from financials import fetch_financials_batch
from constants import OUTPUT_DIR, FILE_NAME, COLUMNS_TO_PRESERVE, FINAL_COLUMNS

# --- Configuration ---
BATCH_SIZE = 39
SLEEP_RANGE = (40, 50)

def prepare_ticker(ticker: str) -> str:
  """Standardizes ticker format based on region."""
  ticker = str(ticker).strip()
  if ticker.endswith('.T') or get_region() != 'JP':
    return ticker
  return f"{ticker}.T"

def process_batches(tickers: List[str]) -> List[Dict]:
  """Handles batch fetching with randomized cooldowns."""
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

    # Cooldown logic
    if batch_num < total_batches:
      wait = random.uniform(*SLEEP_RANGE)
      print(f"   Cooling down for {wait:.1f}s...")
      time.sleep(wait)
      
  return results

def load_and_clean_data(file_path: str) -> pd.DataFrame:
  """Loads CSV and performs initial filtering."""
  if not os.path.exists(file_path):
    raise FileNotFoundError(f"File not found: {file_path}")

  df = pd.read_csv(file_path, encoding='utf-8-sig')
  if df.empty:
    raise ValueError("CSV file is empty.")

  # Filter columns and drop duplicates
  existing_cols = [c for c in COLUMNS_TO_PRESERVE if c in df.columns]
  df = df[existing_cols].copy()
  
  ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
  df = df.drop_duplicates(subset=[ticker_col], keep='first')
  
  # Generate API-ready tickers
  df['api_ticker'] = df[ticker_col].apply(prepare_ticker)
  return df

def main():
  file_path = os.path.join(OUTPUT_DIR, FILE_NAME)
  
  try:
    # 1. Extraction
    df = load_and_clean_data(file_path)
    
    # 2. Transformation (API Fetching)
    print(f"--- Starting Update for {len(df)} Tickers ---")
    financial_results = process_batches(df['api_ticker'].tolist())
    
    # 3. Merging
    # Use reindex to ensure all FINAL_COLUMNS exist without a manual loop
    financials_df = pd.DataFrame(financial_results)
    final_df = pd.concat([
      df.reset_index(drop=True), 
      financials_df.reset_index(drop=True)
    ], axis=1)
    
    final_df = final_df.reindex(columns=FINAL_COLUMNS)

    # 4. Loading (Save to Disk)
    final_df.to_csv(file_path, index=False)
    print(f"\n[+] Update successful! Saved to {file_path}")

  except PermissionError:
    print(f" [!] Error: Close {FILE_NAME} before running.")
  except Exception as e:
    print(f" [!] Process failed: {e}")

if __name__ == "__main__":
  main()
