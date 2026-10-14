"""Extract the top holdings from standardized data/cleansed_ETFs CSVs.

The ETF cleanser owns source parsing, column normalization and weight
calculation. This task keeps rows with a ticker and exchange, optionally
filters by cumulative weight, and writes country CSVs deduplicated by ticker.
Run through the project runner with MODE = EXTRACT_TICKERS.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .constants import (
    EXCHANGES_PATH,
    HOLDINGS_COLUMNS,
    HOLDINGS_DIR,
    LABEL_SUFFIXES,
    OUTPUT_DIR,
    OUTPUT_FIELDS,
    OUTPUT_FILENAME_PATTERN,
    PLACEHOLDER_TOKENS,
    TOP_WEIGHT_PERCENT,
    FILTER_BY_CUMULATIVE_WEIGHT,
    UNKNOWN_REGION,
)
from .fields import Fields

logger = logging.getLogger(__name__)


def normalize_text(value) -> str:
    """Trim whitespace/quotes/leading apostrophes (common from Excel/CSV
    exports) and return a clean string, or "" for anything blank/NaN/NA/NaT
    or a placeholder token like '-' or 'N/A'."""
    if value is None:
        return ""
    try:
        # Catches float('nan'), pandas.NA, and pandas.NaT alike -- a plain
        # isinstance(value, float) check (the previous approach) misses
        # pd.NA/pd.NaT, which then get stringified into the literal text
        # "<NA>" / "NaT" instead of being treated as blank.
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass  # pd.isna() raises on some array-likes; not expected here for scalar cells
    s = str(value).strip()
    s = s.replace("\u3000", " ").strip()  # full-width space, seen in some JP exports
    s = s.lstrip("'").strip().strip('"').strip("'").strip()
    if s.lower() in PLACEHOLDER_TOKENS:
        return ""
    return s


def normalize_ticker(value) -> str:
    """Comparison key only; the source ticker is preserved in output."""
    return normalize_text(value).upper()


def normalize_exchange(value) -> str:
    return normalize_text(value).upper()


@lru_cache(maxsize=1)
def load_exchange_countries() -> Dict[str, str]:
    """Map exact long/short exchange aliases to ISO country codes.

    Shared aliases spanning multiple countries (such as Euronext) remain
    UNKNOWN instead of choosing whichever row happens to come last.
    """
    df = pd.read_csv(EXCHANGES_PATH, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"long_name", "short_name", "country"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{EXCHANGES_PATH}: missing columns: {', '.join(sorted(missing))}")
    countries_by_alias: Dict[str, set] = {}
    for row in df.to_dict("records"):
        country = normalize_text(row["country"]).upper()
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError(f"{EXCHANGES_PATH}: invalid country code {country!r}")
        for field in ("long_name", "short_name"):
            alias = normalize_exchange(row[field])
            if alias:
                countries_by_alias.setdefault(alias, set()).add(country or UNKNOWN_REGION)
    return {
        alias: next(iter(countries)) if len(countries) == 1 else UNKNOWN_REGION
        for alias, countries in countries_by_alias.items()
    }


def get_region(exchange: str) -> str:
    """Return the listing exchange's country, or UNKNOWN for unmapped aliases."""
    return load_exchange_countries().get(normalize_exchange(exchange), UNKNOWN_REGION)


# --------------------------------------------------------------------------
# Cumulative-weight selection
# --------------------------------------------------------------------------


def select_top_indices(weights: List[Optional[float]]) -> Tuple[List[int], bool]:
    """Keep the largest holdings through the one reaching TOP_WEIGHT_PERCENT.

    If usable weights cannot reach the target, retain all rows and flag the
    fallback. Do not rescale incomplete weights to make them sum to 100%.
    """
    order = sorted(
        (i for i, weight in enumerate(weights)
         if weight is not None and math.isfinite(weight) and weight > 0),
        key=lambda i: -weights[i],
    )
    cumulative = 0.0
    for count, i in enumerate(order, 1):
        cumulative += weights[i]
        if cumulative >= TOP_WEIGHT_PERCENT or math.isclose(cumulative, TOP_WEIGHT_PERCENT, rel_tol=0, abs_tol=1e-10):
            return sorted(order[:count]), True
    return list(range(len(weights))), False


# --------------------------------------------------------------------------
# Per-file extraction
# --------------------------------------------------------------------------


@dataclass
class FileResult:
    """Outcome of processing one holdings file, used to build the
    end-of-run summary instead of logging a line per file."""

    records: List[dict]                        # selected source rows, country-tagged
    total_rows: int = 0                          # data rows found before weight selection
    selected_rows: int = 0                       # eligible rows retained by weight selection
    skipped_incomplete: int = 0                  # missing ticker or exchange
    weight_undetermined: bool = False
    duplicate_tickers: int = 0                   # repeated country/ticker across source files
    problem: Optional[str] = None


def extract_holdings(path: Path) -> FileResult:
    """Read the cleanser's fixed schema and keep the top holdings by weight."""
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:
        return FileResult([], problem=f"could not be read ({exc})")

    missing = [field for field in HOLDINGS_COLUMNS if field not in df.columns]
    if missing:
        return FileResult([], problem=f"missing cleansed columns: {', '.join(missing)}")

    df = df.loc[df[HOLDINGS_COLUMNS].ne("").any(axis=1)]
    total_rows = len(df)
    eligible = df[Fields.TICKER].map(normalize_text).ne("") & df[Fields.EXCHANGE].map(normalize_text).ne("")
    skipped_incomplete = int((~eligible).sum())
    df = df.loc[eligible]
    if df.empty:
        return FileResult([], total_rows=total_rows, skipped_incomplete=skipped_incomplete,
                          problem="no holdings with both ticker and exchange")

    top_indices = list(range(len(df)))
    weight_determined = True
    if FILTER_BY_CUMULATIVE_WEIGHT:
        weights = pd.to_numeric(df[Fields.WEIGHT], errors="coerce")
        top_indices, weight_determined = select_top_indices(
            [None if pd.isna(weight) else float(weight) for weight in weights],
        )
    kept = []
    for row in df.iloc[top_indices].to_dict("records"):
        record = {field: row.get(field, "") for field in OUTPUT_FIELDS}
        record[Fields.REGION] = get_region(record[Fields.EXCHANGE])
        kept.append(record)

    return FileResult(
        kept,
        total_rows=total_rows,
        selected_rows=len(top_indices),
        skipped_incomplete=skipped_incomplete,
        weight_undetermined=not weight_determined,
    )


# --------------------------------------------------------------------------
# Run summary
# --------------------------------------------------------------------------


def short_label(path: Path) -> str:
    stem = path.stem
    for suffix in LABEL_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return stem


class RunSummary:
    """Buckets each input file into one category for the end-of-run report,
    keyed by short_label(path)."""

    def __init__(self):
        self.clean: List[str] = []
        self.partial: List[str] = []
        self.problems: Dict[str, List[str]] = {}
        self.region_counts: Dict[str, int] = {}

    def add(self, path: Path, result: FileResult) -> None:
        label = short_label(path)
        if result.problem is not None:
            self.problems.setdefault(result.problem, []).append(label)
            return

        notes = []
        eligible_rows = result.total_rows - result.skipped_incomplete
        if result.weight_undetermined:
            notes.append(f"weights could not reach {TOP_WEIGHT_PERCENT:g}%; kept all {eligible_rows} eligible holdings")
        elif result.selected_rows < eligible_rows:
            notes.append(f"top {TOP_WEIGHT_PERCENT:g}% weight: {result.selected_rows} of {eligible_rows} holdings")
        if result.skipped_incomplete:
            notes.append(f"{result.skipped_incomplete} missing ticker or exchange")
        if result.duplicate_tickers:
            notes.append(f"{result.duplicate_tickers} duplicate ticker(s) collapsed")

        if notes:
            self.partial.append(f"{label} ({', '.join(notes)})")
        else:
            self.clean.append(label)

    def print(self) -> None:
        if self.clean:
            print(f"Successful ETFs ({len(self.clean)}): {', '.join(self.clean)}")
        if self.partial:
            print(f"ETFs with some holdings filtered out ({len(self.partial)}): {', '.join(self.partial)}")
        if self.problems:
            print("Others:")
            for reason, labels in self.problems.items():
                print(f"  {reason} ({len(labels)}): {', '.join(labels)}")
        if self.region_counts:
            ordered = sorted(self.region_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            breakdown = ", ".join(f"{region} ({count})" for region, count in ordered)
            print(f"By region: {breakdown}")


# --------------------------------------------------------------------------
# Main run
# --------------------------------------------------------------------------


def run() -> None:
    # Deliberately no logging.basicConfig() here -- this is a subtask
    # module that may be imported and run by a parent orchestrator, which
    # should own root-logger configuration. This module only logs through
    # its own `logger`; the caller decides where that ends up.
    load_exchange_countries.cache_clear()
    load_exchange_countries()  # Validate the country mapping before processing outputs.
    files = sorted(
        p for p in HOLDINGS_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".csv"
    )
    if not files:
        raise FileNotFoundError(f"no cleansed holdings CSV files found in {HOLDINGS_DIR.resolve()}")

    summary = RunSummary()
    by_region: Dict[str, Dict[str, dict]] = {}  # country -> ticker -> source row

    for path in files:
        result = extract_holdings(path)
        for record in result.records:
            region_rows = by_region.setdefault(record[Fields.REGION], {})
            ticker = normalize_ticker(record[Fields.TICKER])
            if ticker in region_rows:
                result.duplicate_tickers += 1
            # Last occurrence wins, including its source weight and ISIN.
            region_rows[ticker] = record
        summary.add(path, result)

    summary.region_counts = {region: len(rows) for region, rows in by_region.items()}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing_outputs = {
        p for p in OUTPUT_DIR.glob("*.csv") if OUTPUT_FILENAME_PATTERN.match(p.name)
    }

    csv_columns = OUTPUT_FIELDS
    total_stocks = 0
    written_outputs: set = set()
    for region, rows_by_ticker in by_region.items():
        rows = list(rows_by_ticker.values())
        rows.sort(key=lambda r: (r[Fields.TICKER], r[Fields.ISIN], r[Fields.NAME]))
        out_path = OUTPUT_DIR / f"{region}.csv"
        pd.DataFrame(rows, columns=csv_columns).to_csv(out_path, index=False)
        written_outputs.add(out_path)
        total_stocks += len(rows)

    # Remove region files left over from a previous run whose region no
    # longer has any holdings this run, so stale data doesn't linger
    # silently alongside the current output.
    for stale_path in existing_outputs - written_outputs:
        stale_path.unlink()
        logger.info("removed stale output file with no holdings this run: %s", stale_path.name)

    summary.print()
    print(f"Found {total_stocks} unique stocks across the holdings files in {HOLDINGS_DIR.resolve()}.")
    print(f"Written to {len(by_region)} region file(s) in {OUTPUT_DIR.resolve()}")
