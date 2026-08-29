"""
holdings_dedup.py -- standalone script.

Reads every fund/ETF holdings file (.csv, .xlsx) in a directory and writes
out the deduplicated list of stocks found across all of them: Ticker, Name,
ISIN, Exchange, Currency. A stock that appears in multiple holdings files
(or more than once in the same file) is only listed once.

This is a self-contained project with no dependency on any other codebase --
everything it needs (file reading, header detection, normalization, dedup)
lives in this one file.

Usage:
    python holdings_dedup.py <holdings_dir> [-o unique_stocks.csv] [-v]

Example:
    python holdings_dedup.py ./ETFs -o unique_stocks.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# What we're looking for in each file, and how to recognize each column.
# A cell is considered a match for a field if its normalized text contains
# one of that field's candidate strings.
# --------------------------------------------------------------------------
OUTPUT_FIELDS = ["Ticker", "Name", "ISIN", "Exchange", "Currency"]

FIELD_CANDIDATES: Dict[str, List[str]] = {
    "Ticker": ["Code", "Ticker", "銘柄コード", "コード"],
    "Name": ["Name", "銘柄", "銘柄名"],
    "ISIN": ["ISIN"],
    "Exchange": ["Exchange", "取引所"],
    "Currency": ["Currency", "Ccy", "通貨"],
}

# A row is treated as the holdings-table header once at least this many
# distinct fields can be matched to distinct cells in it. 2 is enough to
# rule out a stray cell that happens to contain one field-like word, without
# requiring every field to be present (Exchange/Currency are often absent).
MIN_FIELDS_FOR_HEADER = 2


# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------


def normalize_text(value) -> str:
    """Trim whitespace/quotes/leading apostrophes (common from Excel/CSV
    exports) and return a clean string, or "" for anything blank/NaN."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    s = s.lstrip("'").strip().strip('"').strip("'").strip()
    return s


def normalize_key(value) -> str:
    """Lowercased, whitespace-stripped form used purely for matching."""
    return normalize_text(value).replace("\n", "").replace(" ", "").lower()


def normalize_ticker(value) -> str:
    s = normalize_text(value)
    if not s:
        return ""
    # pandas/Excel can turn numeric tickers into "1605.0"
    if re.fullmatch(r"[+-]?\d+\.0+", s):
        s = s.split(".", 1)[0]
    return s.upper()


def normalize_isin(value) -> str:
    return normalize_text(value).upper()


def normalize_exchange(value) -> str:
    return normalize_text(value).upper()


def normalize_currency(value) -> str:
    return normalize_text(value).upper()


NORMALIZERS = {
    "Ticker": normalize_ticker,
    "Name": normalize_text,
    "ISIN": normalize_isin,
    "Exchange": normalize_exchange,
    "Currency": normalize_currency,
}


# --------------------------------------------------------------------------
# File reading: CSV/XLSX -> a raw grid of cells (no header assumed yet)
# --------------------------------------------------------------------------


def read_raw_grid(path: Path) -> pd.DataFrame:
    """Read a holdings file into a raw grid of cells, tolerating ragged
    rows (metadata rows above the real header often have a different
    number of columns than the data rows)."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        width = max((len(row) for row in rows), default=0)
        padded = [row + [None] * (width - len(row)) for row in rows]
        return pd.DataFrame(padded).replace("", None)

    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, header=None)

    raise ValueError(f"unsupported file format: {suffix}")


# --------------------------------------------------------------------------
# Header-row detection and column lookup
# --------------------------------------------------------------------------


def _cell_matches_field(cell: str, field: str) -> bool:
    cell_norm = normalize_key(cell)
    if not cell_norm:
        return False
    return any(normalize_key(candidate) in cell_norm for candidate in FIELD_CANDIDATES[field])


def find_header_row(raw: pd.DataFrame) -> Optional[int]:
    """Return the index of the first row that looks like a holdings-table
    header, or None if no row qualifies (see MIN_FIELDS_FOR_HEADER)."""
    for i, row in raw.iterrows():
        matched_cells = set()
        matched_fields = 0
        for field in FIELD_CANDIDATES:
            for j, cell in enumerate(row):
                if j in matched_cells or pd.isna(cell):
                    continue
                if _cell_matches_field(str(cell), field):
                    matched_cells.add(j)
                    matched_fields += 1
                    break
        if matched_fields >= MIN_FIELDS_FOR_HEADER:
            return i
    return None


def find_column(columns: Iterable, field: str):
    """Return the actual column label matching `field`, or None."""
    candidates = FIELD_CANDIDATES[field]

    # exact match first
    for candidate in candidates:
        key = normalize_key(candidate)
        for col in columns:
            if normalize_key(col) == key:
                return col

    # substring fallback
    for col in columns:
        col_norm = normalize_key(col)
        for candidate in candidates:
            cand_norm = normalize_key(candidate)
            if cand_norm and cand_norm in col_norm:
                return col

    return None


# --------------------------------------------------------------------------
# Per-file extraction
# --------------------------------------------------------------------------


def extract_stocks(path: Path) -> List[dict]:
    """Return a list of {Ticker, Name, ISIN, Exchange, Currency} dicts, one
    per holding row found in `path`. A column missing from the file is left
    blank ("") for every row; a single row missing just one value is still
    kept, with only that value blank. Rows blank across every field
    (leftover blank/footer lines) are dropped."""
    try:
        raw = read_raw_grid(path)
    except Exception as exc:
        logger.warning("%s: could not be read (%s); skipping.", path.name, exc)
        return []

    header_row = find_header_row(raw)
    if header_row is None:
        logger.warning("%s: no recognizable holdings header found; skipping.", path.name)
        return []

    df = raw.iloc[header_row + 1 :].copy()
    df.columns = raw.iloc[header_row].tolist()
    df = df.reset_index(drop=True).dropna(how="all")

    col_for_field = {field: find_column(df.columns, field) for field in OUTPUT_FIELDS}
    if all(col is None for col in col_for_field.values()):
        logger.warning("%s: none of %s found; skipping.", path.name, ", ".join(OUTPUT_FIELDS))
        return []

    records = []
    for _, row in df.iterrows():
        record = {}
        for field in OUTPUT_FIELDS:
            col = col_for_field[field]
            record[field] = NORMALIZERS[field](row[col]) if col is not None else ""
        if any(record.values()):
            records.append(record)

    return records


# --------------------------------------------------------------------------
# Cross-file dedup
# --------------------------------------------------------------------------


def dedupe_key(record: dict) -> str:
    """Identify a stock for dedup purposes. ISIN is the most reliable
    globally-unique identifier, so it's preferred; fall back to Ticker for
    holdings files that don't report ISIN; fall back to Name as a last
    resort so a stock with neither still doesn't collide with an unrelated
    one that also has neither."""
    if record["ISIN"]:
        return f"isin:{record['ISIN']}"
    if record["Ticker"]:
        return f"ticker:{record['Ticker']}"
    return f"name:{normalize_key(record['Name'])}"


def collect_unique_stocks(holdings_dir: Path) -> List[dict]:
    files = sorted(
        p for p in holdings_dir.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls")
    )
    if not files:
        raise FileNotFoundError(f"no holdings files (.csv/.xlsx) found in {holdings_dir.resolve()}")

    seen: Dict[str, dict] = {}
    for path in files:
        logger.info("Reading %s", path.name)
        for record in extract_stocks(path):
            key = dedupe_key(record)
            if key in seen:
                logger.debug("Duplicate stock skipped: %s (already seen)", record)
                continue
            seen[key] = record

    return list(seen.values())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read a directory of fund/ETF holdings files and output the deduplicated list of stocks found across them."
    )
    parser.add_argument("holdings_dir", type=Path, help="Directory containing holdings files (.csv/.xlsx)")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("unique_stocks.csv"), help="Output CSV path (default: unique_stocks.csv)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log which file each row came from and why files were skipped")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s | %(message)s")

    stocks = collect_unique_stocks(args.holdings_dir)
    stocks.sort(key=lambda r: (r["Ticker"], r["ISIN"], r["Name"]))

    pd.DataFrame(stocks, columns=OUTPUT_FIELDS).to_csv(args.output, index=False)

    print(f"Found {len(stocks)} unique stocks across the holdings files in {args.holdings_dir.resolve()}.")
    print(f"Written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
