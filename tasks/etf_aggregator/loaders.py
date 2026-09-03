from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .constants import AGGREGATE_COLUMNS, ETF_DIR, OUTPUT_DIR, STOCK_FILE_SUFFIX
from .normalize import normalize_ticker

logger = logging.getLogger(__name__)


def load_stock_files() -> Dict[str, pd.DataFrame]:
    """Load stock CSV database files from the output directory into region DataFrames."""
    stock_files = sorted(OUTPUT_DIR.glob(f"*{STOCK_FILE_SUFFIX}"))
    if not stock_files:
        raise FileNotFoundError(f"No *{STOCK_FILE_SUFFIX} files found in {OUTPUT_DIR.resolve()}")

    stock_data: Dict[str, pd.DataFrame] = {}

    for path in stock_files:
        region = path.name[: -len(STOCK_FILE_SUFFIX)].upper()
        logger.info("Loading stock file: %s -> region %s", path, region)
        df = pd.read_csv(path, dtype=str)

        if "Ticker" not in df.columns:
            logger.warning("Skipping %s: missing 'Ticker' column", path)
            continue

        df["_ticker"] = df["Ticker"].map(normalize_ticker)

        for col in AGGREGATE_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        duplicates = df["_ticker"].duplicated(keep=False) & df["_ticker"].ne("")
        if duplicates.any():
            logger.warning(
                "%s contains %d duplicate ticker rows; keeping first occurrence.",
                path.name,
                duplicates.sum(),
            )
            df = df.drop_duplicates("_ticker", keep="first")

        stock_data[region] = df.set_index("_ticker", drop=False)

    if not stock_data:
        raise RuntimeError("No usable stock files loaded.")

    return stock_data


def find_holdings_file(ticker: str) -> Optional[Path]:
    """Find a holdings file for `ticker`, any extension.

    Holdings files follow `{ticker}_{anything}.ext` (the trailing part is
    optional), e.g. "1655_sp500.csv" or plain "VT.xlsx". A file matches if
    the first underscore-delimited token of its filename equals the ticker
    -- extension and everything after the first underscore are irrelevant
    to matching.
    """
    ticker = normalize_ticker(ticker)

    matches = sorted(
        p for p in ETF_DIR.iterdir() if p.is_file() and normalize_ticker(p.stem.split("_")[0]) == ticker
    )

    if not matches:
        return None
    if len(matches) > 1:
        logger.warning("%s: multiple holdings files found; using %s", ticker, matches[0].name)
    return matches[0]
