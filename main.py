import os
import pandas as pd
from processor import load_and_clean_data, process_batches
from constants import OUTPUT_DIR, FILE_NAME, FINAL_COLUMNS

def main():
  file_path = os.path.join(OUTPUT_DIR, FILE_NAME)
  
  try:
    # 1. Extraction
    df = load_and_clean_data(file_path)
    
    # 2. Transformation
    print(f"--- Starting Update for {len(df)} Tickers ---")
    financial_results = process_batches(df['api_ticker'].tolist())
    
    # 3. Merging & Alignment
    financials_df = pd.DataFrame(financial_results)
    final_df = pd.concat([
      df.reset_index(drop=True), 
      financials_df.reset_index(drop=True)
    ], axis=1)
    
    final_df = final_df.reindex(columns=FINAL_COLUMNS)

    # 4. Loading
    final_df.to_csv(file_path, index=False)
    print(f"\n[+] Update successful! Saved to {file_path}")

  except PermissionError:
    print(f" [!] Error: Close {FILE_NAME} before running.")
  except Exception as e:
    print(f" [!] Process failed: {e}")

if __name__ == "__main__":
  main()
