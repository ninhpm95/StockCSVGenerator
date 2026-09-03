"""
run.py -- ETF holdings -> resolved ticker/ISIN extractor.

Expected location: parent/tasks/ticker_extractor/run.py

Reads every ETF holdings file (.csv, .xlsx) in HOLDINGS_DIR. For each file:

  1. Find the header row (same detection as before). Every row below it is
     treated as a holdings data row -- no more "does this look like a
     stock name" filtering.
  2. Work out each row's weight in the fund:
       - a Weight/% of NAV column if the file has one ("8.8" and "8.8%"
         both mean 8.8% == 0.088), otherwise
       - Stock Price x No. of Shares, normalized against the same product
         summed across every row in the file.
     Weight is *only* used to pick the top 100 holdings, so if a file has
     100 rows or fewer we don't bother computing it at all.
  3. Keep the top 100 holdings by weight (or all of them, if <=100).
  4. Work out each kept holding's region (ISIN prefix > Location column >
     Exchange name > UNKNOWN -- unchanged from before).
  5. Look up that region's canonical reference table,
     data/stocks/{region}_lookup.csv:
       - if the holding has an ISIN, look it up by ISIN and overwrite the
         holding's Ticker with the reference Ticker.
       - if it doesn't, look it up by Ticker and fill in the ISIN from the
         reference row.
       - if it has neither ISIN nor Ticker, or the lookup has no match,
         the holding is dropped.
  6. Append the resolved holding to output/{region}.csv, deduplicated by
     ISIN across every source file that contributes to that region.

Usage:
    This is a subtask of the larger project and is not meant to be run as
    a standalone script -- the `from .constants import *` below requires
    it to be executed in its package context (e.g. invoked by the parent
    project's runner, or via `python -m parent.tasks.ticker_extractor.run`
    from REPO_ROOT). Edit HOLDINGS_DIR / LOOKUP_DIR / OUTPUT_DIR in
    constants.py if your layout differs.
"""
from __future__ import annotations

import csv
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from .constants import *

import pandas as pd

logger = logging.getLogger(__name__)


def normalize_text(value) -> str:
    """Trim whitespace/quotes/leading apostrophes (common from Excel/CSV
    exports) and return a clean string, or "" for anything blank/NaN/a
    placeholder token like '-' or 'N/A'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
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
    "Ticker": normalize_ticker,
    "Name": normalize_text,
    "ISIN": normalize_isin,
    "Exchange": normalize_exchange,
    "Currency": normalize_currency,
}


def parse_percentage(value) -> Optional[float]:
    """"8.8" and "8.8%" both mean 8.8% -> 0.088. Returns None if the cell
    doesn't hold a parseable number."""
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
    used in the data/stocks/*_lookup.csv reference files."""
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
    """True if every keyword in `combination` occurs (case-sensitive
    substring) in `cells`, in order, at strictly increasing cell indices.
    Once keyword N is found in cell j, the search for keyword N+1 only
    looks at cells after j -- matched cells don't need to be adjacent."""
    search_from = 0
    for keyword in combination:
        found_at = None
        for j in range(search_from, len(cells)):
            if keyword in cells[j]:
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

    for candidate in candidates:
        key = normalize_key(candidate)
        for col in columns:
            if normalize_key(col) == key:
                return col

    for col in columns:
        col_norm = normalize_key(col)
        for candidate in candidates:
            cand_norm = normalize_key(candidate)
            if cand_norm and cand_norm in col_norm:
                return col

    return None


# --------------------------------------------------------------------------
# Region detection: ISIN country prefix > Location column > Exchange name > UNKNOWN
# --------------------------------------------------------------------------


def _match_region(text: str, mapping: Dict[str, str]) -> Optional[str]:
    text_upper = text.upper()
    for keyword, code in mapping.items():
        if keyword in text_upper:
            return code
    return None


def get_region(isin: str, location: str, exchange: str) -> str:
    """ISIN's 2-letter ISO 6166 country prefix, then a Location/Country
    column, then the Exchange name. Returns UNKNOWN_REGION if none of
    these resolve it."""
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


class FileResult:
    """Outcome of processing one holdings file, used to build the
    end-of-run summary instead of logging a line per file."""

    def __init__(
        self,
        records: List[dict],
        total_rows: int = 0,
        skipped_no_id: int = 0,
        lookup_skipped: int = 0,
        weight_undetermined: bool = False,
        problem: Optional[str] = None,
    ):
        self.records = records                       # kept, region-tagged, pre-lookup
        self.total_rows = total_rows                  # data rows found before top-N cut
        self.skipped_no_id = skipped_no_id             # top-N rows with neither Ticker nor ISIN
        self.lookup_skipped = lookup_skipped           # filled in later, after cross-referencing
        self.weight_undetermined = weight_undetermined
        self.problem = problem


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
    df.columns = raw.iloc[header_row].tolist()
    df = df.reset_index(drop=True).dropna(how="all")

    col_for_field = {field: find_column(df.columns, field) for field in OUTPUT_FIELDS}
    if all(col is None for col in col_for_field.values()):
        return FileResult([], problem=f"none of {', '.join(OUTPUT_FIELDS)} found")

    location_col = find_column(df.columns, "Location")
    weight_col = find_column(df.columns, "Weight")
    price_col = find_column(df.columns, "Price")
    shares_col = find_column(df.columns, "Shares")

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

    # No explicit weight column -- derive weight from price x shares,
    # normalized against every row in the file.
    if weight_col is None and price_col is not None and shares_col is not None:
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
        if not record["Ticker"] and not record["ISIN"]:
            skipped_no_id += 1
            continue
        region = get_region(record["ISIN"], rows[i]["location"], record["Exchange"])
        kept.append({**record, "Region": region})

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
    empty dicts if no lookup file exists for this region."""
    path = LOOKUP_DIR / f"{region}_lookup.csv"
    if not path.exists():
        return {}, {}

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    by_isin: Dict[str, dict] = {}
    by_ticker: Dict[str, dict] = {}
    for _, row in df.iterrows():
        rec = {
            "Ticker": normalize_ticker(row.get("Ticker", "")),
            "Name": normalize_text(row.get("Name", "")),
            "ISIN": normalize_isin(row.get("ISIN", "")),
            "Exchange": normalize_exchange(row.get("Exchange", "")),
            "Currency": normalize_currency(row.get("Currency", "")),
        }
        if rec["ISIN"]:
            by_isin[rec["ISIN"]] = rec
        if rec["Ticker"] and rec["ISIN"]:
            by_ticker[rec["Ticker"]] = rec
    return by_isin, by_ticker


def resolve_holding(record: dict) -> Optional[dict]:
    """Cross-reference one holding against its region's lookup table.
    Returns the resolved holding, or None if it should be skipped (no
    Ticker/ISIN to key off of, or no match found)."""
    by_isin, by_ticker = load_lookup(record["Region"])

    if record["ISIN"]:
        match = by_isin.get(record["ISIN"])
        if not match:
            return None
        resolved = dict(record)
        if match["Ticker"]:
            resolved["Ticker"] = match["Ticker"]
        return resolved

    if record["Ticker"]:
        match = by_ticker.get(record["Ticker"])
        if not match:
            return None
        resolved = dict(record)
        resolved["ISIN"] = match["ISIN"]
        return resolved

    return None  # no ISIN and no Ticker -- shouldn't reach here, but be safe


# --------------------------------------------------------------------------
# Run summary
# --------------------------------------------------------------------------

_LABEL_SUFFIXES = ["_brd_data", "_holdings"]


def short_label(path: Path) -> str:
    stem = path.stem
    for suffix in _LABEL_SUFFIXES:
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
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

    files = sorted(
        p for p in HOLDINGS_DIR.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls")
    )
    if not files:
        raise FileNotFoundError(f"no holdings files (.csv/.xlsx) found in {HOLDINGS_DIR.resolve()}")

    summary = RunSummary()
    by_region: Dict[str, Dict[str, dict]] = {}  # region -> ISIN -> resolved record

    for path in files:
        result = extract_holdings(path)

        lookup_skipped = 0
        for record in result.records:
            resolved = resolve_holding(record)
            if resolved is None:
                lookup_skipped += 1
                continue
            by_region.setdefault(resolved["Region"], {})[resolved["ISIN"]] = resolved
        result.lookup_skipped = lookup_skipped

        summary.add(path, result)

    summary.region_counts = {region: len(rows) for region, rows in by_region.items()}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_columns = OUTPUT_FIELDS + ["Region"]
    total_stocks = 0
    for region, rows_by_isin in by_region.items():
        rows = list(rows_by_isin.values())
        rows.sort(key=lambda r: (r["Ticker"], r["ISIN"], r["Name"]))
        for r in rows:
            r["Ticker"] = format_ticker_for_output(r["Ticker"])
        out_path = OUTPUT_DIR / f"{region}.csv"
        pd.DataFrame(rows, columns=csv_columns).to_csv(out_path, index=False)
        total_stocks += len(rows)

    summary.print()
    print(f"Found {total_stocks} unique stocks across the holdings files in {HOLDINGS_DIR.resolve()}.")
    print(f"Written to {len(by_region)} region file(s) in {OUTPUT_DIR.resolve()}")
