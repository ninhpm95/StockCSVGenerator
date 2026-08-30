from __future__ import annotations

import logging

import pandas as pd

from .constants import (
    AGGREGATE_COLUMNS,
    LOG_PATH,
    OUTPUT_DIR,
    OUTPUT_FILE,
    TARGET_ETF_FILE,
)
from .loaders import load_stock_files
from .normalize import normalize_ticker
from .processor import process_etf
from .stats import ETFStats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
logger = logging.getLogger(__name__)


def run() -> None:
    target_path = OUTPUT_DIR / TARGET_ETF_FILE
    output_path = OUTPUT_DIR / OUTPUT_FILE

    if not target_path.exists():
        raise FileNotFoundError(f"ETF file missing: {target_path.resolve()}")

    logger.info("Loading stock databases...")
    stock_data = load_stock_files()

    logger.info("Loading ETF file: %s", target_path)
    etfs = pd.read_csv(target_path, dtype=str)

    if "Ticker" not in etfs.columns:
        raise ValueError(f"{TARGET_ETF_FILE} must contain a 'Ticker' column.")

    original_columns = list(etfs.columns)

    for col in AGGREGATE_COLUMNS:
        if col in etfs.columns:
            etfs[col] = pd.to_numeric(etfs[col], errors="coerce")

    total_stats = ETFStats()
    updated_rows = []
    skipped: list[str] = []
    match_summary: list[tuple[str, int, int, float]] = []  # Added float for weight

    for _, etf_row in etfs.iterrows():
        total_stats.etfs += 1
        ticker = normalize_ticker(etf_row.get("Ticker", ""))

        updated, row_stats = process_etf(etf_row, stock_data)
        total_stats += row_stats
        updated_rows.append(updated)

        if row_stats.holdings == 0:
            skipped.append(ticker)
        else:
            match_summary.append((ticker, row_stats.matched, row_stats.holdings, row_stats.matched_weight))

    result = pd.DataFrame(updated_rows)[original_columns]
    result.to_csv(output_path, index=False)

    logger.info("Output saved to: %s", output_path.resolve())
    total_stats.log_summary(logger)

    _print_summary(output_path, skipped, match_summary)


def _print_summary(
    output_path,
    skipped: list[str],
    match_summary: list[tuple[str, int, int, float]],
) -> None:
    """Print a compact, scannable summary to the terminal. All the detail
    (per-holding misses, parsing errors, etc.) lives in LOG_PATH instead."""
    print(f"Output saved to: {output_path.resolve()}")
    print(f"Full log: {LOG_PATH.resolve()}")
    print()
    print(f"Skipped ETFs ({len(skipped)}): {', '.join(sorted(skipped)) if skipped else 'none'}")
    print()
    print("Matching:")
    for ticker, matched, holdings, matched_weight in sorted(match_summary):
        pct = round(matched_weight * 100) if matched_weight <= 1.0 + 1e-9 else round(matched_weight)
        print(f"{ticker}: {matched}/{holdings} holdings ({pct}%)")

    print()
    print(f"Success! Aggregated data successfully written to: {output_path.resolve()}")
