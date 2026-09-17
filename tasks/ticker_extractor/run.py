"""
run.py -- ETF holdings -> resolved ticker/ISIN extractor.

Expected location: parent/tasks/ticker_extractor/run.py

Reads every ETF holdings file (.csv, .xlsx, .xls) in HOLDINGS_DIR. For each
file:

  1. Find the header row (same detection as before). Every row below it is
     treated as a holdings data row -- no more "does this look like a
     stock name" filtering.
  2. Work out each row's weight in the fund:
       - a Weight/% of NAV column if the file has one and its values are
         parseable ("8.8" and "8.8%" both mean 8.8% == 0.088), otherwise
       - Stock Price x No. of Shares, normalized against the same product
         summed across every row in the file.
     Weight is *only* used to pick the top 100 holdings, so if a file has
     100 rows or fewer we don't bother computing it at all.
  3. Keep the top 100 holdings by weight (or all of them, if <=100).
  4. Work out each kept holding's region (ISIN prefix > Location column >
     Exchange name > filename convention > UNKNOWN -- unchanged from
     before).
  5. Look up that region's canonical reference table,
     data/stocks/{region}_lookup.csv:
       - if the holding has an ISIN, look it up by ISIN and overwrite the
         holding's Ticker with the reference Ticker.
       - if it doesn't, look it up by Ticker and fill in the ISIN from the
         reference row.
       - if it has neither ISIN nor Ticker, or the lookup has no match,
         the holding is dropped.
     Either way, any of Name/Exchange/Currency the holding itself left
     blank is backfilled from the matched reference row.
  6. Append the resolved holding to output/{region}.csv, deduplicated by
     ISIN across every source file that contributes to that region.

Known limitation: parse_percentage() assumes a bare number in a Weight
column means a percentage ("8.8" -> 8.8%), matching every provider seen so
far. A provider that instead exports weight as a raw decimal fraction
(e.g. "0.088" meaning 8.8%) would be misread as 0.088%. If that ever shows
up, parse_percentage needs a per-provider or per-file override.

Usage:
    This is a subtask of the larger project and is not meant to be run as
    a standalone script -- the imports below require it to be executed in
    its package context (e.g. invoked by the parent project's runner, or
    via `python -m parent.tasks.ticker_extractor.run` from REPO_ROOT).
    Edit HOLDINGS_DIR / LOOKUP_DIR / OUTPUT_DIR in constants.py if your
    layout differs. This module does not call logging.basicConfig() --
    the calling process is expected to configure logging handlers/level;
    this module only logs through its own `logger`.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from .constants import (
    CSV_ENCODINGS,
    EXCHANGE_TO_REGION,
    FIELD_CANDIDATES,
    GLOBAL_REGION,
    HEADER_KEYWORD_COMBINATIONS,
    HOLDINGS_DIR,
    HOLDINGS_SHEET_NAMES,
    LABEL_SUFFIXES,
    LOCATION_TO_REGION,
    LOOKUP_DIR,
    OUTPUT_DIR,
    OUTPUT_FIELDS,
    PLACEHOLDER_TOKENS,
    TOP_N_HOLDINGS,
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
    Fields.TICKER: normalize_ticker,
    Fields.NAME: normalize_text,
    Fields.ISIN: normalize_isin,
    Fields.EXCHANGE: normalize_exchange,
    Fields.CURRENCY: normalize_currency,
}


def parse_percentage(value) -> Optional[float]:
    """"8.8" and "8.8%" both mean 8.8% -> 0.088. Returns None if the cell
    doesn't hold a parseable number. See the module docstring for the
    decimal-fraction ambiguity this assumes away."""
    s = normalize_text(value)
    if not s:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def parse_number(value) -> Optional[float]:
    """Parse a price/share-count cell into a float, or None."""
    s = normalize_text(value)
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def format_ticker_for_output(ticker: str) -> str:
    """Tickers that start with '0' lose their leading zero when a plain
    CSV is opened in Excel unless the cell is forced to text -- prefix
    with a single quote in that case, matching the convention already
    used in the data/stocks/*_lookup.csv reference files.

    Only call this at the final CSV-writing step (see run(), below) --
    never before a Ticker value is used for lookup matching, or the
    leading quote would break exact-match comparisons against
    data/stocks/*_lookup.csv (which does not store the quote)."""
    if ticker.startswith("0"):
        return f"'{ticker}"
    return ticker


def _resolve_holdings_sheet(xls: pd.ExcelFile, path: Path) -> str:
    for candidate in HOLDINGS_SHEET_NAMES:
        for name in xls.sheet_names:
            if str(name).strip() == candidate:
                return name

    for name in xls.sheet_names:
        preview = pd.read_excel(path, sheet_name=name, header=None, nrows=50)
        if find_header_row(preview) is not None:
            return name

    return xls.sheet_names[0]


def _read_csv_rows(path: Path) -> List[List[str]]:
    """Read a CSV's rows as raw strings, trying each of CSV_ENCODINGS in
    order until one decodes without error. Different providers export in
    different encodings (e.g. iShares in UTF-8, Next Funds in Shift-JIS)
    and don't declare which, so we sniff by trying."""
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in CSV_ENCODINGS:
        try:
            with open(path, newline="", encoding=encoding) as f:
                return list(csv.reader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error  # every configured encoding failed to decode


def read_raw_grid(path: Path) -> pd.DataFrame:
    """Read a holdings file into a raw grid of cells, tolerating ragged
    rows (metadata rows above the real header often have a different
    number of columns than the data rows)."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        rows = _read_csv_rows(path)
        width = max((len(row) for row in rows), default=0)
        padded = [row + [None] * (width - len(row)) for row in rows]
        return pd.DataFrame(padded).replace("", None)

    if suffix in (".xlsx", ".xls"):
        xls = pd.ExcelFile(path)
        sheet_name = _resolve_holdings_sheet(xls, path)
        return pd.read_excel(path, sheet_name=sheet_name, header=None)

    raise ValueError(f"unsupported file format: {suffix}")


# --------------------------------------------------------------------------
# Header-row detection and column lookup
# --------------------------------------------------------------------------


def _row_matches_combination(cells: List[str], combination: Tuple[str, ...]) -> bool:
    """True if every keyword in `combination` occurs (case-insensitive
    substring) in `cells`, in order, at strictly increasing cell indices.
    Once keyword N is found in cell j, the search for keyword N+1 only
    looks at cells after j -- matched cells don't need to be adjacent."""
    search_from = 0
    for keyword in combination:
        keyword_key = keyword.lower()
        found_at = None
        for j in range(search_from, len(cells)):
            if keyword_key in cells[j].lower():
                found_at = j
                break
        if found_at is None:
            return False
        search_from = found_at + 1
    return True


def find_header_row(raw: pd.DataFrame) -> Optional[int]:
    """Return the index of the first row that looks like a holdings-table
    header, or None if no row qualifies.

    Scans row by row, top to bottom. For each row, tries each sequence in
    HEADER_KEYWORD_COMBINATIONS in order; the row is a header as soon as
    any combination matches (see _row_matches_combination)."""
    for i, row in raw.iterrows():
        cells = ["" if pd.isna(cell) else str(cell) for cell in row]
        if any(_row_matches_combination(cells, combo) for combo in HEADER_KEYWORD_COMBINATIONS):
            return i
    return None


def find_column(columns: Iterable, field: str):
    """Return the actual column label matching `field`, or None."""
    candidates = FIELD_CANDIDATES[field]
    columns = list(columns)

    # Pass 1: exact (normalized) match, trying candidates in priority order.
    for candidate in candidates:
        key = normalize_key(candidate)
        for col in columns:
            if normalize_key(col) == key:
                return col

    # Pass 2: substring match. Candidates are tried in priority order
    # *outer*, columns inner, so a generic candidate (e.g. "Code" for
    # Ticker) only gets a chance to match once every more specific
    # candidate for this field has failed to match ANY column -- it can no
    # longer win against a more specific candidate just because it happens
    # to sit on an earlier column (e.g. "Country Code" appearing before the
    # real "Ticker Symbol" column).
    for candidate in candidates:
        cand_norm = normalize_key(candidate)
        if not cand_norm:
            continue
        for col in columns:
            if cand_norm in normalize_key(col):
                return col

    return None


def _dedupe_columns(labels: List[str]) -> List[str]:
    """Append a numeric suffix to repeated column labels so that
    `df[col]` always returns a single Series rather than silently
    returning a DataFrame (and `row[col]` a Series) when a source file has
    two columns with the same header text."""
    seen: Dict[str, int] = {}
    deduped = []
    for label in labels:
        count = seen.get(label, 0)
        seen[label] = count + 1
        deduped.append(label if count == 0 else f"{label}__dup{count}")
    return deduped


# --------------------------------------------------------------------------
# Region detection: ISIN country prefix > Location column > Exchange name >
# filename convention > UNKNOWN
# --------------------------------------------------------------------------


def _match_region(text: str, mapping: Dict[str, str]) -> Optional[str]:
    text_upper = text.upper()
    for keyword, code in mapping.items():
        if keyword in text_upper:
            return code
    return None


def _region_from_filename(path: Path) -> Optional[str]:
    """Return a manually supplied region from a holdings filename.

    Some providers (notably Maxis) use filenames such as
    ``221A_JP_Maxis_JpSemi.csv`` where the region cannot be inferred from
    the holding rows themselves. In that convention, the region is the
    second underscore-separated token in the filename stem (immediately
    after the ETF's own ticker, which is always first).

    NOTE: this positional convention is inferred from the one example
    documented for this fallback (Maxis). It intentionally does NOT try to
    locate a "ticker" token within the filename by matching it against
    anything from the holdings rows -- a per-holding ticker (an underlying
    stock in the ETF) will essentially never appear in the ETF's own
    filename, which made the original version of this fallback dead code
    in practice. If a provider with a different filename convention shows
    up, this function needs updating.
    """
    parts = path.stem.split("_")
    if len(parts) < 2:
        return None
    candidate = normalize_text(parts[1]).upper()
    if re.fullmatch(r"[A-Z]{2}", candidate):
        return candidate
    return None


def get_region(isin: str, location: str, exchange: str, path: Optional[Path] = None) -> str:
    """Resolve a holding's region in priority order.

    Priority:
      1. ISIN country prefix
      2. Location/Country column
      3. Exchange name
      4. Region manually encoded in the filename (e.g.
         ``221A_JP_Maxis_JpSemi.csv`` -> ``JP``)
      5. UNKNOWN
    """
    if len(isin) >= 2 and isin[:2].isalpha():
        return isin[:2].upper()

    if location:
        region = _match_region(location, LOCATION_TO_REGION)
        if region:
            return region

    if exchange:
        region = _match_region(exchange, EXCHANGE_TO_REGION)
        if region:
            return region

    if path is not None:
        region = _region_from_filename(path)
        if region:
            return region

    return UNKNOWN_REGION


# --------------------------------------------------------------------------
# Weight computation / top-N selection
# --------------------------------------------------------------------------


def select_top_indices(weights: List[Optional[float]], limit: int) -> Tuple[List[int], bool]:
    """Return the indices of the top `limit` rows by weight (all indices,
    in original order, if there are <= limit rows), plus whether weight was
    actually determinable for the ranking used."""
    n = len(weights)
    if n <= limit:
        return list(range(n)), True

    if all(w is None for w in weights):
        # Can't rank at all -- fall back to the first `limit` rows as-is
        # rather than dropping the file entirely.
        return list(range(limit)), False

    order = sorted(range(n), key=lambda i: (weights[i] is None, -(weights[i] or 0.0)))
    return sorted(order[:limit]), True


# --------------------------------------------------------------------------
# Per-file extraction
# --------------------------------------------------------------------------


@dataclass
class FileResult:
    """Outcome of processing one holdings file, used to build the
    end-of-run summary instead of logging a line per file."""

    records: List[dict]                        # kept, region-tagged, pre-lookup
    total_rows: int = 0                          # data rows found before top-N cut
    skipped_no_id: int = 0                       # top-N rows with neither Ticker nor ISIN
    lookup_skipped: int = 0                      # filled in later, after cross-referencing
    weight_undetermined: bool = False
    duplicate_isins: int = 0                     # same ISIN appearing twice within this file
    problem: Optional[str] = None


def extract_holdings(path: Path) -> FileResult:
    """Return a FileResult with the top TOP_N_HOLDINGS holdings (by weight)
    found in `path`, region-tagged and ready for cross-referencing against
    data/stocks/*_lookup.csv. Every row from the header down counts as a
    holding -- unlike before, there's no non-equity-name filtering."""
    try:
        raw = read_raw_grid(path)
    except Exception as exc:
        return FileResult([], problem=f"could not be read ({exc})")

    header_row = find_header_row(raw)
    if header_row is None:
        return FileResult([], problem="no recognizable holdings header found")

    df = raw.iloc[header_row + 1:].copy()
    header_values = ["" if pd.isna(v) else str(v) for v in raw.iloc[header_row]]
    df.columns = _dedupe_columns(header_values)
    df = df.reset_index(drop=True).dropna(how="all")

    col_for_field = {field: find_column(df.columns, field) for field in OUTPUT_FIELDS}
    if all(col is None for col in col_for_field.values()):
        return FileResult([], problem=f"none of {', '.join(OUTPUT_FIELDS)} found")

    location_col = find_column(df.columns, Fields.LOCATION)
    weight_col = find_column(df.columns, Fields.WEIGHT)
    price_col = find_column(df.columns, Fields.PRICE)
    shares_col = find_column(df.columns, Fields.SHARES)

    rows = []
    for _, row in df.iterrows():
        record = {
            field: NORMALIZERS[field](row[col]) if col is not None else ""
            for field, col in col_for_field.items()
        }
        if not any(record.values()):
            continue  # fully blank row -- not even a name, ignore silently

        location = normalize_text(row[location_col]) if location_col is not None else ""
        weight = parse_percentage(row[weight_col]) if weight_col is not None else None
        price = parse_number(row[price_col]) if price_col is not None else None
        shares = parse_number(row[shares_col]) if shares_col is not None else None
        rows.append({"record": record, "location": location, "weight": weight, "price": price, "shares": shares})

    if not rows:
        return FileResult([], problem="no data rows found")

    # Fall back to deriving weight from price x shares (normalized against
    # every row in the file) whenever the Weight column didn't give us
    # anything usable -- either because there was no Weight column at all,
    # or because one was detected but none of its values parsed (e.g. an
    # unexpected format). Without this "or" clause, a file with a garbled
    # Weight column would fall back to "first N rows" even when Price and
    # Shares were both available and usable.
    have_weight = any(r["weight"] is not None for r in rows)
    if not have_weight and price_col is not None and shares_col is not None:
        caps = [
            (r["price"] * r["shares"]) if r["price"] is not None and r["shares"] is not None else None
            for r in rows
        ]
        total = sum(v for v in caps if v is not None)
        for r, cap in zip(rows, caps):
            r["weight"] = (cap / total) if (cap is not None and total) else None

    weights = [r["weight"] for r in rows]
    top_indices, weight_determined = select_top_indices(weights, TOP_N_HOLDINGS)

    kept = []
    skipped_no_id = 0
    for i in top_indices:
        record = rows[i]["record"]
        if not record[Fields.TICKER] and not record[Fields.ISIN]:
            skipped_no_id += 1
            continue
        region = get_region(
            record[Fields.ISIN],
            rows[i]["location"],
            record[Fields.EXCHANGE],
            path=path,
        )
        kept.append({**record, Fields.REGION: region})

    return FileResult(
        kept,
        total_rows=len(rows),
        skipped_no_id=skipped_no_id,
        weight_undetermined=(len(rows) > TOP_N_HOLDINGS and not weight_determined),
    )


# --------------------------------------------------------------------------
# Cross-referencing against data/stocks/{region}_lookup.csv
# --------------------------------------------------------------------------


@lru_cache(maxsize=None)
def load_lookup(region: str) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """Load data/stocks/{region}_lookup.csv into (by_isin, by_ticker) dicts
    of normalized {Ticker, Name, ISIN, Exchange, Currency} rows. Returns two
    empty dicts if no lookup file exists for this region.

    Column names are matched with the same find_column() logic used for
    holdings files (rather than requiring exact "Ticker"/"Name"/... headers)
    so a lookup CSV with slightly different casing/spacing doesn't
    silently produce empty records."""
    path = LOOKUP_DIR / f"{region}_lookup.csv"
    if not path.exists():
        return {}, {}

    df = None
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in CSV_ENCODINGS:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if df is None:
        raise last_error  # every configured encoding failed to decode

    col_for_field = {field: find_column(df.columns, field) for field in OUTPUT_FIELDS}

    by_isin: Dict[str, dict] = {}
    by_ticker: Dict[str, dict] = {}
    for _, row in df.iterrows():
        rec = {
            field: NORMALIZERS[field](row[col]) if col is not None else ""
            for field, col in col_for_field.items()
        }
        if rec[Fields.ISIN]:
            by_isin[rec[Fields.ISIN]] = rec
        if rec[Fields.TICKER] and rec[Fields.ISIN]:
            by_ticker[rec[Fields.TICKER]] = rec
    return by_isin, by_ticker


def resolve_holding(record: dict) -> Optional[dict]:
    """Cross-reference one holding against its region's lookup table. If
    the region's table has no match (or the region has no lookup file at
    all), fall back to GLOBAL_lookup.csv, which covers every stock, before
    giving up. GLOBAL_lookup.csv is large, so it's only loaded (once, then
    cached via load_lookup's lru_cache) the first time a fallback is
    actually needed -- files for regions that never miss are never touched.

    Any of Name/Exchange/Currency the holding itself left blank is
    backfilled from the matched reference row.

    Returns the resolved holding, or None if it should be skipped (no
    Ticker/ISIN to key off of, or no match found in either table)."""
    by_isin, by_ticker = load_lookup(record[Fields.REGION])

    if record[Fields.ISIN]:
        match = by_isin.get(record[Fields.ISIN])
        if not match and record[Fields.REGION] != GLOBAL_REGION:
            global_by_isin, _ = load_lookup(GLOBAL_REGION)
            match = global_by_isin.get(record[Fields.ISIN])
        if not match:
            return None
        resolved = dict(record)
        if match[Fields.TICKER]:
            resolved[Fields.TICKER] = match[Fields.TICKER]
        _backfill_blank_fields(resolved, match)
        return resolved

    if record[Fields.TICKER]:
        match = by_ticker.get(record[Fields.TICKER])
        used_global = False
        if not match and record[Fields.REGION] != GLOBAL_REGION:
            _, global_by_ticker = load_lookup(GLOBAL_REGION)
            match = global_by_ticker.get(record[Fields.TICKER])
            used_global = True
        if not match:
            return None
        resolved = dict(record)
        resolved[Fields.ISIN] = match[Fields.ISIN]
        _backfill_blank_fields(resolved, match)
        if used_global and resolved[Fields.ISIN]:
            # The region-specific lookup had nothing for this ticker, so
            # `record[Fields.REGION]` was set by get_region() without any
            # ISIN to go on (often UNKNOWN). Now that GLOBAL_lookup.csv has
            # given us a real ISIN, re-derive the region from its country
            # prefix -- otherwise the holding lands in output/UNKNOWN.csv
            # even when its ISIN clearly says e.g. US.
            new_region = get_region(resolved[Fields.ISIN], "", "")
            if new_region != UNKNOWN_REGION:
                resolved[Fields.REGION] = new_region
        return resolved

    return None  # no ISIN and no Ticker -- shouldn't reach here, but be safe


def _backfill_blank_fields(resolved: dict, match: dict) -> None:
    """Fill Name/Exchange/Currency on `resolved` from `match` wherever
    `resolved` itself is blank, in place. Ticker/ISIN are handled by the
    caller since their overwrite rules differ (Ticker is always taken from
    the reference on an ISIN match; ISIN is always taken from the
    reference on a Ticker match)."""
    for f in (Fields.NAME, Fields.EXCHANGE, Fields.CURRENCY):
        if not resolved.get(f) and match.get(f):
            resolved[f] = match[f]


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
        if result.total_rows > TOP_N_HOLDINGS:
            if result.weight_undetermined:
                notes.append(f"top {TOP_N_HOLDINGS} of {result.total_rows} (weight undetermined, used first {TOP_N_HOLDINGS})")
            else:
                notes.append(f"top {TOP_N_HOLDINGS} of {result.total_rows} by weight")
        if result.skipped_no_id:
            notes.append(f"{result.skipped_no_id} no ticker/ISIN")
        if result.lookup_skipped:
            notes.append(f"{result.lookup_skipped} no lookup match")
        if result.duplicate_isins:
            notes.append(f"{result.duplicate_isins} duplicate ISIN(s) collapsed")

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
    files = sorted(
        p for p in HOLDINGS_DIR.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls")
    )
    if not files:
        raise FileNotFoundError(f"no holdings files (.csv/.xlsx/.xls) found in {HOLDINGS_DIR.resolve()}")

    summary = RunSummary()
    by_region: Dict[str, Dict[str, dict]] = {}  # region -> ISIN -> resolved record

    for path in files:
        result = extract_holdings(path)

        lookup_skipped = 0
        duplicate_isins = 0
        seen_isins_this_file: set = set()
        for record in result.records:
            resolved = resolve_holding(record)
            if resolved is None:
                lookup_skipped += 1
                continue
            isin = resolved[Fields.ISIN]
            if isin in seen_isins_this_file:
                duplicate_isins += 1
                logger.warning(
                    "%s: duplicate ISIN %s within this file; last occurrence wins",
                    short_label(path), isin,
                )
            seen_isins_this_file.add(isin)
            by_region.setdefault(resolved[Fields.REGION], {})[isin] = resolved
        result.lookup_skipped = lookup_skipped
        result.duplicate_isins = duplicate_isins

        summary.add(path, result)

    summary.region_counts = {region: len(rows) for region, rows in by_region.items()}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing_outputs = set(OUTPUT_DIR.glob("*.csv"))

    csv_columns = OUTPUT_FIELDS + [Fields.REGION]
    total_stocks = 0
    written_outputs: set = set()
    for region, rows_by_isin in by_region.items():
        rows = list(rows_by_isin.values())
        rows.sort(key=lambda r: (r[Fields.TICKER], r[Fields.ISIN], r[Fields.NAME]))
        for r in rows:
            r[Fields.TICKER] = format_ticker_for_output(r[Fields.TICKER])
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
