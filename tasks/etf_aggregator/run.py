from __future__ import annotations

import os
import tempfile

import pandas as pd

import logging

from .constants import AGGREGATE_COLUMNS, TARGET_ETF_FILE
from .loaders import load_stock_files
from .normalize import normalize_ticker
from .processor import (
    SKIP_EMPTY_HOLDINGS,
    SKIP_LOW_COVERAGE,
    SKIP_NO_HOLDINGS_FILE,
    SKIP_NO_TICKER,
    SKIP_PARSE_ERROR,
    SKIP_PROCESSING_ERROR,
    process_etf,
)
from .stats import ETFStats

logger = logging.getLogger(__name__)

# Human-readable labels for the SKIP_* reason codes, in the order they
# should be displayed in the terminal summary.
_SKIP_REASON_LABELS = [
    (SKIP_NO_HOLDINGS_FILE, "No holdings file found"),
    (SKIP_EMPTY_HOLDINGS, "Holdings file has no usable holdings"),
    (SKIP_PARSE_ERROR, "Error while parsing holdings file"),
    (SKIP_PROCESSING_ERROR, "Unexpected error during matching/aggregation"),
    (SKIP_NO_TICKER, "ETF row has no ticker"),
]


def _format_pct(matched_weight: float) -> int:
    """Round a matched-weight fraction to a display percentage, clamping
    away float rounding noise just over 100% (e.g. a holdings file whose
    weights sum to 100.0002% due to rounding). The clamp is applied to the
    raw float *before* rounding -- rounding first and then checking the
    rounded value would miss cases like 101.9999%, which rounds to 102 and
    is no longer caught by a post-rounding "< 102" check."""
    raw_pct = matched_weight * 100
    if 100.0 < raw_pct < 102.0:
        raw_pct = 100.0
    return round(raw_pct)


def _print_summary(
    target_path, # TARGET_ETF_FILE
    skipped: list[tuple[str, str]], # Stores (ticker, skip_reason)
    low_coverage: list[tuple[str, int, int, float]], # Stores (ticker, matched_count, total_holdings, matched_weight)
    match_summary: list[tuple[str, int, int, float]], # Stores (ticker, matched_count, total_holdings, matched_weight)
) -> None:
    """Print a compact, scannable summary to the terminal. All the detail
    (per-holding misses, parsing errors, etc.) lives in the log file instead,
    when file logging is enabled by whatever configures logging at the
    project's entry point."""
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
    print(f"Low coverage, aggregation skipped ({len(low_coverage)}):")
    if not low_coverage:
        print(" none")
    else:
        for ticker, matched, holdings, matched_weight in sorted(low_coverage):
            print(f"  {ticker}: {matched}/{holdings} holdings ({_format_pct(matched_weight)}%)")

    print()
    print("Matching:")
    for ticker, matched, holdings, matched_weight in sorted(match_summary):
        print(f"{ticker}: {matched}/{holdings} holdings ({_format_pct(matched_weight)}%)")

    print()
    print("Success!")


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
    low_coverage: list[tuple[str, int, int, float]] = []  # (ticker, matched, holdings, matched_weight)
    match_summary: list[tuple[str, int, int, float]] = []  # (ticker, matched, holdings, matched_weight)

    for _, etf_row in etfs.iterrows():
        total_stats.etfs += 1
        ticker = normalize_ticker(etf_row.get("Ticker", ""))

        updated, row_stats, skip_reason = process_etf(etf_row, stock_data)
        total_stats += row_stats
        updated_rows.append(updated)

        row_summary = (ticker, row_stats.matched, row_stats.holdings, row_stats.matched_weight)
        if skip_reason == SKIP_LOW_COVERAGE:
            low_coverage.append(row_summary)
        elif skip_reason:
            # Covers all zero-holdings skip codes AND SKIP_PROCESSING_ERROR,
            # which can fire even when holdings > 0 (matching/aggregation
            # failed after parsing succeeded) -- routing by skip_reason
            # rather than by row_stats.holdings == 0 keeps that case out
            # of match_summary, where it doesn't belong.
            skipped.append((ticker, skip_reason))
        else:
            match_summary.append(row_summary)

    # reindex rather than plain [] column selection: updated_rows are
    # pd.Series (not dicts) and an empty updated_rows list produces a
    # DataFrame with zero columns, which raises KeyError on []. reindex
    # handles both that empty case and any per-row index quirks safely.
    result = pd.DataFrame(updated_rows).reindex(columns=original_columns)

    # Write to a temp file in the same directory and atomically replace the
    # target with it, rather than writing target_path directly. target_path
    # is both this run's output and next run's input, so a crash or
    # interruption mid-write (disk full, killed process, ...) would
    # otherwise leave a truncated/corrupt file in place with no input left
    # to recover from.
    target_dir = target_path.resolve().parent
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_" + target_path.name, dir=target_dir)
    try:
        os.close(fd)
        result.to_csv(tmp_name, index=False)
        os.replace(tmp_name, target_path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise

    logger.info("Output saved to: %s", target_path.resolve())
    total_stats.log_summary(logger)

    _print_summary(target_path, skipped, low_coverage, match_summary)
