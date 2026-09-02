from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import pandas as pd

from .constants import (
    CODE_COLUMN_CANDIDATES,
    EXCHANGE_COLUMN_CANDIDATES,
    HEADER_KEYWORD_COMBINATIONS,
    HOLDINGS_SHEET_NAMES,
    ISIN_COLUMN_CANDIDATES,
    NAME_COLUMN_CANDIDATES,
    PRICE_COLUMN_CANDIDATES,
    REGION_COLUMN_CANDIDATES,
    SHARES_COLUMN_CANDIDATES,
    VALUATION_COLUMN_CANDIDATES,
    WEIGHT_COLUMN_CANDIDATES,
)
from .normalize import (
    normalize_exchange,
    normalize_isin,
    normalize_region,
    normalize_text,
    normalize_ticker,
    is_reserved_non_stock,
    is_valid_isin,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Column / header discovery helpers
# --------------------------------------------------------------------------


def find_column(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    """Find a column name using exact or substring normalized text matching.

    Candidate priority is enforced in two full passes, not interleaved:
    first every candidate is checked for an EXACT match (in candidate
    order), and only if none match at all is a second pass run checking
    every candidate for a SUBSTRING match (again in candidate order). This
    matters because a lower-priority candidate could substring-match some
    column while a higher-priority candidate is still waiting to be tried
    -- e.g. the candidate "Code" would substring-match a stray column
    before the exact candidate "銘柄コード（Code）" got a chance, if the two
    passes weren't kept separate like this.
    """
    columns = list(columns)
    candidates = list(candidates)
    normalized = {_norm_key(c): c for c in columns}

    # Pass 1: exact match, in candidate priority order.
    for candidate in candidates:
        key = _norm_key(candidate)
        if key in normalized:
            return normalized[key]

    # Pass 2: substring match, in candidate priority order.
    for candidate in candidates:
        cand_norm = _norm_key(candidate)
        if not cand_norm:
            continue
        for col in columns:
            if cand_norm in _norm_key(col):
                return col

    return None


def _norm_key(value: str) -> str:
    return normalize_text(value).replace("\n", "").replace(" ", "").lower()


def _row_cells(row: Iterable) -> List[str]:
    """Normalize each cell in a row to text, dropping blanks."""
    cells = (normalize_text(cell).replace("\n", " ") for cell in row if not pd.isna(cell))
    return [cell for cell in cells if cell]


def _matches_combo(cells: List[str], combo: tuple) -> bool:
    """Check if keywords in `combo` appear in distinct cells in left-to-right order (case-sensitive)."""
    last_index = -1
    for keyword in combo:
        # Performs exact case-sensitive substring matching
        found_in = [i for i, cell in enumerate(cells) if i > last_index and keyword in cell]
        if not found_in:
            return False
        last_index = found_in[0]
    return True


def _find_header_row(rows: Iterable[Iterable], limit: Optional[int] = None) -> Optional[int]:
    """Return the index of the row that looks like the holdings table header.

    Tries HEADER_KEYWORD_COMBINATIONS in priority order: every row is
    checked against combination 1 first (across the whole file/preview);
    only if nothing matches at all is combination 2 tried, then
    combination 3. Within whichever combination matches, the first row in
    file order wins. Rows are materialized once so multiple combinations
    can be scanned without re-reading the source.
    """
    materialized = []
    for i, row in enumerate(rows):
        if limit is not None and i >= limit:
            break
        materialized.append(_row_cells(row))

    for combo in HEADER_KEYWORD_COMBINATIONS:
        for i, cells in enumerate(materialized):
            if _matches_combo(cells, combo):
                return i

    return None


def _extract(df: pd.DataFrame, col: Optional[str], mapper: Callable, default=""):
    """Apply `mapper` to `col` if it was found, else return `default` for every row."""
    return df[col].map(mapper) if col else default


def parse_number(value: str | float | None) -> float:
    """Parse a string number, stripping thousands separators and a trailing
    '%' sign. This does NOT rescale percentages -- "50%" becomes 50, not
    0.5 -- because whether a "%"-formatted field should be treated as a
    fraction depends on the caller. Use `parse_percent` when you actually
    want a 0-1 fraction.
    """
    if pd.isna(value):
        return float("nan")

    cleaned = str(value).strip().replace(",", "").replace("%", "")
    if not cleaned:
        return float("nan")

    try:
        return float(cleaned)
    except ValueError:
        return float("nan")

def parse_percent(value: str | float | None) -> float:
    """Parse a percentage field into a 0-1 fraction.

    Handles two distinct representations:
    - Native numeric values from Excel cells with a "%" number format
      (e.g. a cell displaying "8.05%") are already stored as a 0-1
      fraction (0.0805) by the time pandas reads them -- these must NOT
      be divided by 100 again.
    - Text values (from CSV, or Excel cells stored as plain text/numbers
      without percent formatting), e.g. "27.78%" or "27.78", still need
      the "%" stripped and the number divided by 100.
    """
    if pd.isna(value):
        return float("nan")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    n = parse_number(value)
    return n / 100.0 if pd.notna(n) else float("nan")


# --------------------------------------------------------------------------
# Raw grid loading (format-specific I/O only; everything after this point
# is format-agnostic)
# --------------------------------------------------------------------------


def _find_holdings_sheet(xls: pd.ExcelFile, path: Path) -> str:
    for candidate in HOLDINGS_SHEET_NAMES:
        for name in xls.sheet_names:
            if str(name).strip() == candidate:
                return name

    # Fall back to whichever sheet actually contains a recognizable header row.
    for name in xls.sheet_names:
        preview = pd.read_excel(path, sheet_name=name, header=None, nrows=50)
        if _find_header_row(preview.itertuples(index=False), limit=50) is not None:
            return name

    raise ValueError(f"{path.name}: could not find holdings sheet in {xls.sheet_names}")


def _read_csv_grid(path: Path) -> pd.DataFrame:
    """Read a CSV into a raw grid, tolerating ragged rows.

    We don't yet know where the header row is (that's what _find_header_row
    figures out next), so we can't tell pandas how many columns to expect.
    pd.read_csv infers a column count from the first lines and raises
    ParserError on any row with a different count -- common here since
    metadata rows above the real header often have a different number of
    commas than the data rows. The csv module has no such expectation, so
    we use it directly and pad short rows out to the widest row seen.

    Empty cells are mapped to NaN (matching pd.read_csv's default behavior)
    so downstream blank-row/column cleanup and pd.isna checks still work.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    width = max((len(row) for row in rows), default=0)
    padded = [row + [None] * (width - len(row)) for row in rows]
    return pd.DataFrame(padded).replace("", None)


def _read_raw_grid(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_grid(path)
    if suffix == ".xlsx":
        xls = pd.ExcelFile(path)
        sheet_name = _find_holdings_sheet(xls, path)
        return pd.read_excel(path, sheet_name=sheet_name, header=None)
    raise ValueError(f"Unsupported file format: {path}")


# --------------------------------------------------------------------------
# Holdings parsing (single implementation for CSV and XLSX)
# --------------------------------------------------------------------------


_HOLDINGS_COLUMNS = ["Code", "ISIN", "Name", "Shares", "Price", "Valuation", "Exchange", "Region", "pct", "_source"]


def _empty_holdings_frame(path: Path) -> pd.DataFrame:
    """A zero-row DataFrame with the standard holdings schema, used when a
    file can't be matched to any tickers -- downstream code (processor.py)
    already handles zero holdings the same way it handles a missing file."""
    return pd.DataFrame(columns=_HOLDINGS_COLUMNS)


def parse_holdings(path: Path) -> pd.DataFrame:
    """Parse an ETF holdings file (CSV or XLSX) into a standard DataFrame with
    columns: Code, ISIN, Name, Shares, Price, Valuation, Exchange, Region,
    pct, _source.

    Requires enough to determine each holding's portfolio weight (a
    Weight/% column, or both Shares and Price) -- that's a real parsing
    failure and raises. A missing Code/Ticker column is treated
    differently: some funds (typically foreign-holdings-only ones) simply
    never report a per-holding ticker, only ISIN -- that's not malformed
    data, so it degrades to zero holdings rather than raising.
    """
    raw = _read_raw_grid(path)
    header_row = _find_header_row(raw.itertuples(index=False))

    if header_row is None:
        logger.warning("%s: could not find a holdings table header; treating as zero holdings.", path.name)
        return _empty_holdings_frame(path)

    df = raw.iloc[header_row + 1 :].copy()
    df.columns = raw.iloc[header_row].tolist()
    df = df.reset_index(drop=True).dropna(how="all")

    code_col = find_column(df.columns, CODE_COLUMN_CANDIDATES)
    isin_col = find_column(df.columns, ISIN_COLUMN_CANDIDATES)
    name_col = find_column(df.columns, NAME_COLUMN_CANDIDATES)
    shares_col = find_column(df.columns, SHARES_COLUMN_CANDIDATES)
    price_col = find_column(df.columns, PRICE_COLUMN_CANDIDATES)
    valuation_col = find_column(df.columns, VALUATION_COLUMN_CANDIDATES)
    weight_col = find_column(df.columns, WEIGHT_COLUMN_CANDIDATES)
    exchange_col = find_column(df.columns, EXCHANGE_COLUMN_CANDIDATES)
    region_col = find_column(df.columns, REGION_COLUMN_CANDIDATES)

    if code_col is None:
        logger.warning(
            "%s: no Code/Ticker column found (Found: %s); this fund's holdings can't be "
            "matched to tickers, treating as zero holdings.",
            path.name,
            list(df.columns),
        )
        return _empty_holdings_frame(path)

    if weight_col is None and (shares_col is None or price_col is None):
        raise ValueError(
            f"{path.name}: need a Weight/% column, or both Shares and Price columns, "
            f"to determine holding weights. Found: {list(df.columns)}"
        )

    result = pd.DataFrame(
        {
            "Code": df[code_col].map(normalize_ticker),
            "ISIN": _extract(df, isin_col, normalize_isin),
            "Name": _extract(df, name_col, normalize_text),
            "Shares": _extract(df, shares_col, parse_number, default=float("nan")),
            "Price": _extract(df, price_col, parse_number, default=float("nan")),
            "Valuation": _extract(df, valuation_col, parse_number, default=float("nan")),
            "Exchange": _extract(df, exchange_col, normalize_exchange),
            "Region": _extract(df, region_col, normalize_region),
            "_source": path.name,
        }
    )

    if weight_col is not None:
        result["pct"] = df[weight_col].map(parse_percent)
    else:
        valid_code = result["Code"].ne("")
        market_value = result["Shares"] * result["Price"]
        total_value = market_value[valid_code].sum(skipna=True)
        result["pct"] = market_value / total_value if total_value > 0 else float("nan")

    # Enforce numeric type on weight
    result["pct"] = pd.to_numeric(result["pct"], errors="coerce")

    # Filter out footer junk and non-stock lines (cash, currency balances,
    # collateral, ...): the Code must be present and not a known non-stock
    # placeholder, the Name must be present, weight must be positive, and
    # -- when an ISIN was actually provided -- it must look like a real
    # ISIN. A blank ISIN doesn't fail the check: plenty of funds simply
    # don't report one per holding.
    valid_holdings = (
        result["Code"].ne("") &
        ~result["Code"].apply(is_reserved_non_stock) &
        result["Name"].ne("") &
        result["pct"].notna() &
        (result["pct"] > 0) &
        (result["ISIN"].eq("") | result["ISIN"].apply(is_valid_isin))
    )

    return result[valid_holdings].reset_index(drop=True)
