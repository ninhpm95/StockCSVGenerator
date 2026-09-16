from __future__ import annotations

import pandas as pd

import logging

from .constants import AGGREGATE_COLUMNS, TARGET_ETF_FILE
from .loaders import load_stock_files
from .normalize import normalize_ticker
from .processor import SKIP_EMPTY_HOLDINGS, SKIP_NO_HOLDINGS_FILE, SKIP_NO_TICKER, SKIP_PARSE_ERROR, process_etf
from .stats import ETFStats

logger = logging.getLogger(__name__)

# Human-readable labels for the SKIP_* reason codes, in the order they
# should be displayed in the terminal summary.
_SKIP_REASON_LABELS = [
    (SKIP_NO_HOLDINGS_FILE, "No holdings file found"),
    (SKIP_EMPTY_HOLDINGS, "Holdings file has no usable holdings"),
    (SKIP_PARSE_ERROR, "Error while parsing holdings file"),
    (SKIP_NO_TICKER, "ETF row has no ticker"),
]


def _print_summary(
    target_path, # TARGET_ETF_FILE
    skipped: list[tuple[str, str]], # Stores (ticker, skip_reason)
    match_summary: list[tuple[str, int, int, float]], # Stores (ticker, matched_count, total_holdings, matched_weight)
) -> None:
    """Print a compact, scannable summary to the terminal. All the detail
    (per-holding misses, parsing errors, etc.) lives in the log file instead,
    when file logging is enabled -- see logging_config.py at the project
    root, which prints the log file's path itself if one was created."""
    print(f"Output saved to: {target_path.resolve()}")
    print()
    print(f"Skipped ETFs ({len(skipped)}):")
    if not skipped:
        print(" none")
    else:
        by_reason: dict[str, list[str]] = {}
        for ticker, reason in skipped:
            by_reason.setdefault(reason, []).append(ticker)

        for reason_code, label in _SKIP_REASON_LABELS:
            tickers = sorted(by_reason.pop(reason_code, []))
            if tickers:
                print(f"  {label} ({len(tickers)}): {', '.join(tickers)}")

        # Anything with an unrecognized/blank reason code (shouldn't
        # normally happen, but don't silently drop tickers if it does).
        for reason_code, tickers in by_reason.items():
            label = reason_code or "unknown reason"
            print(f"  {label} ({len(tickers)}): {', '.join(sorted(tickers))}")
    print()
    print("Matching:")
    for ticker, matched, holdings, matched_weight in sorted(match_summary):
        pct = round(matched_weight * 100)
        if pct > 100 and pct < 102:
            pct = 100 # Round up to 100% if it's just a rounding error (e.g. 101.9999%).
        print(f"{ticker}: {matched}/{holdings} holdings ({pct}%)")

    print()
    print(f"Success! Aggregated data successfully written to: {target_path.resolve()}")


def run() -> None:
    # TARGET_ETF_FILE is already absolute (built from OUTPUT_DIR in
    # constants.py). This is now also the output path: results are written
    # back in place rather than to a separate details file.
    target_path = TARGET_ETF_FILE

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
    skipped: list[tuple[str, str]] = []  # (ticker, skip_reason)
    match_summary: list[tuple[str, int, int, float]] = []  # Added float for weight

    for _, etf_row in etfs.iterrows():
        total_stats.etfs += 1
        ticker = normalize_ticker(etf_row.get("Ticker", ""))

        updated, row_stats, skip_reason = process_etf(etf_row, stock_data)
        total_stats += row_stats
        updated_rows.append(updated)

        if row_stats.holdings == 0:
            skipped.append((ticker, skip_reason))
        else:
            match_summary.append((ticker, row_stats.matched, row_stats.holdings, row_stats.matched_weight))

    result = pd.DataFrame(updated_rows)[original_columns]
    result.to_csv(target_path, index=False)

    logger.info("Output saved to: %s", target_path.resolve())
    total_stats.log_summary(logger)

    _print_summary(target_path, skipped, match_summary)
