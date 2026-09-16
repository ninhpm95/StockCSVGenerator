import os
import pandas as pd
from .processor import load_and_clean_data, process_batches
from .helper import get_region
from .constants import OUTPUT_DIR, FINAL_COLUMNS

def run(file_name: str, speed: str):
    file_path = os.path.join(OUTPUT_DIR, file_name)
    region = get_region(file_path)

    try:
        # 1. Extraction
        df = load_and_clean_data(file_path)

        # 2. Transformation
        print(f"--- Starting Update for {len(df)} Tickers ---")
        financial_results = process_batches(df['api_ticker'].tolist(), region, speed)

        # 3. Merging & Alignment
        financials_df = pd.DataFrame(financial_results)
        final_df = pd.concat([
            df.reset_index(drop=True),
            financials_df.reset_index(drop=True)
        ], axis=1)

        final_df = final_df.reindex(columns=FINAL_COLUMNS)

        # 4. Loading
        # Write to a temp file first, then atomically replace the original.
        # to_csv(file_path) directly would truncate the existing file before
        # writing the new content - a crash or interrupt mid-write loses the
        # original data. os.replace is atomic on both POSIX and Windows, so
        # the original is only ever swapped out once the new file is fully
        # written and closed.
        tmp_path = file_path + ".tmp"
        final_df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, file_path)
        print(f"\n[+] Update successful! Saved to {file_path}")

    except PermissionError:
        print(f" [!] Error: Close {file_name} before running.")
    except Exception as e:
        print(f" [!] Process failed: {e}")
