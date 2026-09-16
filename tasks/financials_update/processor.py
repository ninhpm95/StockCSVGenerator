import math
import time
import random
import os
import pandas as pd
from typing import List, Dict
from .financials import fetch_financials_batch
from .helper import prepare_ticker, get_tv_sleep_range
from .constants import BATCH_SIZE, COLUMNS_TO_PRESERVE
from .fields import TICKER

def load_and_clean_data(file_path: str) -> pd.DataFrame:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    df = pd.read_csv(file_path, encoding='utf-8-sig')
    if df.empty:
        raise ValueError("CSV file is empty.")

    existing_cols = [c for c in COLUMNS_TO_PRESERVE if c in df.columns]
    df = df[existing_cols].copy()
    
    ticker_col = TICKER if TICKER in df.columns else df.columns[0]
    df = df.drop_duplicates(subset=[ticker_col], keep='first')
    
    df['api_ticker'] = df[ticker_col].apply(lambda t: prepare_ticker(t))
    return df

def process_batches(tickers: List[str], region: str, speed: str) -> List[Dict]:
    results = []
    total_batches = math.ceil(len(tickers) / BATCH_SIZE)
    sleep_range = get_tv_sleep_range(len(tickers), speed)

    for i in range(0, len(tickers), BATCH_SIZE):
        batch_num = (i // BATCH_SIZE) + 1
        batch = tickers[i : i + BATCH_SIZE]
        print(f"[*] Processing batch {batch_num}/{total_batches}...")

        try:
            batch_data = fetch_financials_batch(batch, region, speed)
            results.extend(batch_data)
        except Exception as e:
            print(f" [!] Batch {batch_num} failed: {e}")
            # Tag each row with which ticker it was and that it failed,
            # rather than a bare {} - keeps the row count aligned for the
            # positional concat in run.py, but makes a failed batch
            # visible/traceable in the output instead of just blank cells.
            results.extend([{'error': True, 'error_ticker': t} for t in batch])

        if batch_num < total_batches:
            wait = random.uniform(*sleep_range)
            print(f"   Cooling down for {wait:.1f}s...")
            time.sleep(wait)
    return results
