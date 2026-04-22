import os
import time
import math
import random
import pandas as pd
from helper import get_region
from financials import fetch_financials_batch
from constants import OUTPUT_DIR, FILE_NAME, COLUMNS_TO_PRESERVE, FINAL_COLUMNS

def main():
  file_path = os.path.join(OUTPUT_DIR, FILE_NAME)
  
  # Load and Clean
  if not os.path.exists(file_path):
    print(f" [!] File {file_path} not found")
    return

  df = pd.read_csv(file_path, encoding='utf-8-sig')
  
  if df.empty:
    print(" [!] CSV file is empty. Nothing to process.")
    return

  existing_cols = [c for c in COLUMNS_TO_PRESERVE if c in df.columns]
  df = df[existing_cols].copy()
  
  ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
  df = df.drop_duplicates(subset=[ticker_col], keep='first')

  # Prep API tickers
  is_jp = get_region() == 'JP'
  df['api_ticker'] = df[ticker_col].astype(str).apply(
    lambda x: x.strip() if x.endswith('.T') or not is_jp else f"{x.strip()}.T"
  )

  # Fetch Data in Batches
  all_tickers = df['api_ticker'].tolist()
  batch_size = 39
  total_batches = math.ceil(len(all_tickers) / batch_size)
  results = []

  print(f"--- Starting Update for {len(all_tickers)} Tickers ---")
  
  for i in range(0, len(all_tickers), batch_size):
    current_batch_num = (i // batch_size) + 1
    batch = all_tickers[i : i + batch_size]
    
    print(f"[*] Processing batch {current_batch_num}/{total_batches} ({len(batch)} tickers)...")
    
    try:
      batch_data = fetch_financials_batch(batch)
      results.extend(batch_data)
    except Exception as e:
      print(f" [!] Critical error in batch {current_batch_num}: {e}")
      # Fill with empty dicts to keep DataFrame alignment if a whole batch fails
      results.extend([{}] * len(batch))

    # Randomized Cooldown (Skip sleep on the last batch)
    if current_batch_num < total_batches:
      # Randomize sleep to mimic human behavior
      sleep_time = random.uniform(40, 50)
      print(f"    Batch complete. Cooling down for {sleep_time:.1f}s...")
      time.sleep(sleep_time)

  # Merge and Align
  # Ensure indices are reset so the horizontal concat aligns row-by-row correctly
  financials_df = pd.DataFrame(results)
  final_df = pd.concat([df.reset_index(drop=True), financials_df.reset_index(drop=True)], axis=1)

  # Final Formatting and Save
  # Ensure all expected columns exist (fills missing with NaN)
  for col in FINAL_COLUMNS:
    if col not in final_df.columns:
      final_df[col] = None
      
  final_df = final_df[FINAL_COLUMNS]

  try:
    final_df.to_csv(file_path, index=False)
    print(f"\n[+] Update successful! Saved to {file_path}")
  except PermissionError:
    print(f" [!] Error: Could not save file. Please close {FILE_NAME} if it is open in Excel.")

if __name__ == "__main__":
  main()
