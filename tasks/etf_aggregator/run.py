from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .constants import (
    AGGREGATE_COLUMNS,
    ENABLE_LOG_FILE,
    LOGS_DIR,
    TARGET_ETF_FILE,
)
from .loaders import load_stock_files
from .normalize import normalize_ticker
from .processor import (
    SKIP_EMPTY_HOLDINGS,
    SKIP_NO_HOLDINGS_FILE,
    SKIP_NO_TICKER,
    SKIP_PARSE_ERROR,
    process_etf,
)
from .stats import ETFStats

logger = logging.getLogger(__name__)

# Human-readable labels for the SKIP_* reason codes, in the order they
# should be displayed in the terminal summary.
_SKIP_REASON_LABELS = [
    (SKIP_NO_HOLDINGS_FILE, "No holdings file found"),
    (SKIP_EMPTY_HOLDINGS, "Holdings file had no usable holdings (e.g. bond fund, or file couldn't be parsed into rows -- see full log)"),
    (SKIP_PARSE_ERROR, "Error while parsing holdings file -- see full log"),
    (SKIP_NO_TICKER, "ETF row has no Ticker value"),
]


def _configure_logging() -> Optional[Path]:
    """Set up file logging for this run and return the log path, or None if
    file logging is disabled (see constants.ENABLE_LOG_FILE).

    Each run gets its own timestamped file under LOGS_DIR, same as before.

    Deliberately done here, inside run(), rather than at module import
    time: this task's log file should only appear when the task actually
    runs. LOGS_DIR.mkdir() only happens once we know that's the case, and
    delay=True on the FileHandler means the file itself isn't created on
    disk until the first record is actually emitted -- so a run that
    raises before logging anything (e.g. the FileNotFoundError below)
    still won't leave behind an empty log file.

    Any handler added by a previous _configure_logging() call (e.g. run()
    invoked more than once in the same process) is removed first, so
    repeated runs don't pile up duplicate handlers and duplicate log lines.

    When file logging is disabled, a NullHandler is attached instead of
    leaving the root logger with no handlers at all. Without it, Python's
    logging module falls back to its "handler of last resort" and prints
    WARNING-and-above records (missing holdings files, matching misses,
    etc.) straight to stderr -- exactly the noisy detail this toggle is
    meant to suppress. The terminal summary printed by _print_summary()
    uses plain print(), so it's unaffected either way.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for old_handler in root.handlers[:]:
        if getattr(old_handler, "_etf_pipeline_handler", False):
            root.removeHandler(old_handler)
            old_handler.close()

    if not ENABLE_LOG_FILE:
        null_handler = logging.NullHandler()
        null_handler._etf_pipeline_handler = True
        root.addHandler(null_handler)
        return None

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

    handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    handler._etf_pipeline_handler = True

    root.addHandler(handler)

    return log_path


def run() -> None:
    log_path = _configure_logging()

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

    _print_summary(target_path, log_path, skipped, match_summary)


def _print_summary(
    target_path,
    log_path: Optional[Path],
    skipped: list[tuple[str, str]],
    match_summary: list[tuple[str, int, int, float]],
) -> None:
    """Print a compact, scannable summary to the terminal. All the detail
    (per-holding misses, parsing errors, etc.) lives in the log file instead,
    when file logging is enabled (see constants.ENABLE_LOG_FILE)."""
    print(f"Output saved to: {target_path.resolve()}")
    if log_path is not None:
        print(f"Full log: {log_path.resolve()}")
    print()
    print(f"Skipped ETFs ({len(skipped)}):")
    if not skipped:
        print("  none")
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
        pct = round(matched_weight * 100) if matched_weight <= 1.0 + 1e-9 else round(matched_weight)
        print(f"{ticker}: {matched}/{holdings} holdings ({pct}%)")

    print()
    print(f"Success! Aggregated data successfully written to: {target_path.resolve()}")
