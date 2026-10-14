"""Extract unique listings from data/cleansed_ETFs CSVs.

Missing extraction columns are treated as blank; extra columns are ignored.
This task keeps rows with a ticker and exchange, optionally
filters by cumulative weight, and writes country CSVs deduplicated by exchange
and ticker.
Run through the project runner with MODE = EXTRACT_TICKERS.
"""
from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .constants import (
    EXCHANGES_PATH,
    HOLDINGS_DIR,
    OUTPUT_DIR,
    OUTPUT_FIELDS,
    REPO_ROOT,
    PLACEHOLDER_TOKENS,
    TOP_WEIGHT_PERCENT,
    FILTER_BY_CUMULATIVE_WEIGHT,
    MIN_HOLDINGS_FOR_WEIGHT_FILTER,
    UNKNOWN_REGION,
)
from .fields import Fields

CountryListings = Dict[str, Dict[Tuple[str, str], dict]]


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
    """Map exact exchange/short-name aliases to ISO country codes.

    Shared aliases spanning multiple countries (such as Euronext) remain
    UNKNOWN instead of choosing whichever row happens to come last.
    """
    df = pd.read_csv(EXCHANGES_PATH, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"exchange", "short_name", "country"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{EXCHANGES_PATH}: missing columns: {', '.join(sorted(missing))}")
    countries_by_alias: Dict[str, set] = {}
    for row in df.to_dict("records"):
        country = normalize_text(row["country"]).upper()
        if country and not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError(f"{EXCHANGES_PATH}: invalid country code {country!r}")
        for field in ("exchange", "short_name"):
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
    duplicate_listings: int = 0                  # repeated country/exchange/ticker
    problem: Optional[str] = None


def extract_holdings(path: Path) -> FileResult:
    """Extract available fields, filling missing columns with blank values."""
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:
        return FileResult([], problem=f"could not be read ({exc})")

    # Weight is optional input for the cumulative filter, never an output field.
    df = df.reindex(columns=OUTPUT_FIELDS + [Fields.WEIGHT], fill_value="")

    df = df.loc[df[OUTPUT_FIELDS].ne("").any(axis=1)]
    total_rows = len(df)
    eligible = df[Fields.TICKER].map(normalize_text).ne("") & df[Fields.EXCHANGE].map(normalize_text).ne("")
    skipped_incomplete = int((~eligible).sum())
    df = df.loc[eligible]
    if df.empty:
        return FileResult([], total_rows=total_rows, skipped_incomplete=skipped_incomplete,
                          problem="no holdings with both ticker and exchange")

    top_indices = list(range(len(df)))
    weight_determined = True
    if FILTER_BY_CUMULATIVE_WEIGHT and total_rows >= MIN_HOLDINGS_FOR_WEIGHT_FILTER:
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


def choose_listing(existing: dict, candidate: dict) -> dict:
    """Keep the row with more filled output fields; ties keep the first."""
    existing_filled = sum(bool(normalize_text(existing[field])) for field in OUTPUT_FIELDS)
    candidate_filled = sum(bool(normalize_text(candidate[field])) for field in OUTPUT_FIELDS)
    return candidate if candidate_filled > existing_filled else existing


def merge_listings(records: List[dict], by_region: CountryListings) -> int:
    """Merge source rows by country, exchange and ticker; count duplicates."""
    duplicates = 0
    for record in records:
        region_rows = by_region.setdefault(record[Fields.REGION], {})
        key = (normalize_exchange(record[Fields.EXCHANGE]), normalize_ticker(record[Fields.TICKER]))
        if key in region_rows:
            duplicates += 1
            region_rows[key] = choose_listing(region_rows[key], record)
        else:
            region_rows[key] = record
    return duplicates


# --------------------------------------------------------------------------
# Run summary
# --------------------------------------------------------------------------


class RunSummary:
    """Buckets each input file into one category for the end-of-run report,
    labeled with its full filename."""

    def __init__(self):
        self.clean: List[str] = []
        self.partial: List[str] = []
        self.problems: Dict[str, List[str]] = {}
        self.region_counts: Dict[str, int] = {}

    def add(self, path: Path, result: FileResult) -> None:
        label = path.name
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
        if result.duplicate_listings:
            notes.append(f"{result.duplicate_listings} duplicate exchange/ticker pair(s) collapsed")

        if notes:
            self.partial.append(f"{label} ({', '.join(notes)})")
        else:
            self.clean.append(label)

    def print(self) -> None:
        if self.clean:
            print(f"Successful ETFs ({len(self.clean)}):")
            for label in self.clean:
                print(f"  * {label}")
            print()
        if self.partial:
            print(f"ETFs with some holdings filtered out ({len(self.partial)}):")
            for label in self.partial:
                print(f"  * {label}")
            print()
        if self.problems:
            for reason, labels in self.problems.items():
                print(f"{reason[0].upper() + reason[1:]} ({len(labels)}):")
                for label in labels:
                    print(f"  * {label}")
                print()
        if self.region_counts:
            ordered = sorted(self.region_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            breakdown = ", ".join(f"{region} ({count})" for region, count in ordered)
            print(f"By region: {breakdown}")
            print()


# --------------------------------------------------------------------------
# Output writing
# --------------------------------------------------------------------------


def write_outputs(by_region: CountryListings) -> None:
    """Recreate the output folder and write the current country listings."""
    # Check the absolute deletion target before clearing the output directory.
    output_dir = OUTPUT_DIR.resolve()
    repo_root = REPO_ROOT.resolve()
    if output_dir == repo_root or not output_dir.is_relative_to(repo_root):
        raise ValueError(f"output directory must be inside the project: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for region, rows_by_listing in by_region.items():
        rows = list(rows_by_listing.values())
        rows.sort(key=lambda r: (r[Fields.TICKER], r[Fields.EXCHANGE], r[Fields.ISIN], r[Fields.NAME]))
        out_path = output_dir / f"{region}.csv"
        pd.DataFrame(rows, columns=OUTPUT_FIELDS).to_csv(out_path, index=False)


# --------------------------------------------------------------------------
# Main run
# --------------------------------------------------------------------------


def run() -> None:
    load_exchange_countries.cache_clear()
    load_exchange_countries()  # Validate the country mapping before processing outputs.
    files = sorted(
        p for p in HOLDINGS_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".csv"
    )
    if not files:
        raise FileNotFoundError(f"no cleansed holdings CSV files found in {HOLDINGS_DIR.resolve()}")

    summary = RunSummary()
    by_region: CountryListings = {}

    for path in files:
        result = extract_holdings(path)
        result.duplicate_listings = merge_listings(result.records, by_region)
        summary.add(path, result)

    summary.region_counts = {region: len(rows) for region, rows in by_region.items()}
    write_outputs(by_region)

    summary.print()
    total_listings = sum(summary.region_counts.values())
    print(f"Found {total_listings} unique exchange/ticker listings across the holdings files in {HOLDINGS_DIR.resolve()}.")
    print(f"Written to {len(by_region)} region file(s) in {OUTPUT_DIR.resolve()}")
