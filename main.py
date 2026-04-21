import os
import time
import pandas as pd
from helper import get_region
from financials import fetch_financials
from constants import OUTPUT_DIR, FILE_NAME, COLUMNS_TO_PRESERVE, FINAL_COLUMNS

def main():
  file_path = os.path.join(OUTPUT_DIR, FILE_NAME)
  
  # Load and Clean
  if not os.path.exists(file_path):
    print(f"File {file_path} not found")
    return

  df = pd.read_csv(file_path, encoding='utf-8-sig')
  existing_cols = [c for c in COLUMNS_TO_PRESERVE if c in df.columns]
  df = df[existing_cols].copy()
  
  ticker_col = 'Ticker' if 'Ticker' in df.columns else df.columns[0]
  df = df.drop_duplicates(subset=[ticker_col], keep='first')

  # Prep API tickers
  df['api_ticker'] = df[ticker_col].astype(str).apply(
    lambda x: x if x.endswith('.T') or get_region() != 'JP' else f"{x.strip()}.T"
  )

  # Fetch Data
  print(f"Updating financials for {len(df)} tickers...")
  results = []
  for symbol in df['api_ticker']:
    # print(f"Processing: {symbol}")
    data = fetch_financials(symbol)
    results.append(data if data else {})
    time.sleep(0.1) 

  # Merge and Sort
  financials_df = pd.DataFrame(results)
  final_df = pd.concat([df.reset_index(drop=True), financials_df], axis=1)

  # Column Reordering
  final_df = final_df[FINAL_COLUMNS]

  # Save
  final_df.to_csv(file_path, index=False)
  print(f"\nUpdate successful! Data formatted and saved to {file_path}")

if __name__ == "__main__":
  main()
