"""
holdings_dedup.py -- standalone script.

Reads every fund/ETF holdings file (.csv, .xlsx) in HOLDINGS_DIR and writes
out the deduplicated stocks found across all of them, split by region into
one file per region under REGION_OUTPUT_DIR (JP.csv, US.csv, CN.csv, ...,
UNKNOWN.csv for anything that couldn't be placed). Each row has Ticker,
Name, ISIN, Exchange, Currency, Region. A stock that appears in multiple
holdings files (or more than once in the same file) is only listed once.

Ticker is the required identifier -- ISIN is used when present (it's the
more reliable cross-file identifier and the best signal for region) but a
row with no Ticker is dropped even if it has an ISIN, since a ticker is
what you actually need to look the stock up or trade it.

Region is worked out per row, in priority order:
    1. ISIN's 2-letter country prefix (ISO 6166 -- most reliable when present)
    2. A Location/Country column in the holdings file, if it has one
    3. The Exchange column, matched against known exchange names
    4. "UNKNOWN" if none of the above resolve it (still written out, not dropped)

This is a self-contained project with no dependency on any other codebase --
everything it needs (file reading, header detection, normalization, region
lookup, dedup) lives in this one file.

Usage:
    Edit HOLDINGS_DIR / REGION_OUTPUT_DIR below if your layout differs, then:
        python holdings_dedup.py
"""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Input/output locations. These rarely change, so they're plain constants
# rather than CLI arguments -- edit here if your layout differs.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
HOLDINGS_DIR = PROJECT_ROOT.parent.parent / "data" / "ETFs"
REGION_OUTPUT_DIR = PROJECT_ROOT / "output" / "by_region"

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
    # Not in OUTPUT_FIELDS -- Location is only used as a region-detection
    # signal, not written out as its own column.
    "Location": ["Location", "Country", "国", "国名", "所在国", "所在地"],
}

# A row is treated as the holdings-table header once at least this many
# distinct fields can be matched to distinct cells in it. 2 is enough to
# rule out a stray cell that happens to contain one field-like word, without
# requiring every field to be present (Exchange/Currency are often absent).
MIN_FIELDS_FOR_HEADER = 2


# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------



# Tokens that source files use to mean "no value" (seen in place of a real
# ticker/ISIN/name for cash, futures, treasury bills, etc.) rather than
# leaving the cell blank. These normalize to "" -- an *actual* blank --
# so downstream logic (e.g. "does this row have a Ticker or ISIN?") isn't
# fooled into treating a placeholder dash as a real identifier.
PLACEHOLDER_TOKENS = {"-", "--", "―", "‐", "‑", "n/a", "na", "null", "none"}


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


# --------------------------------------------------------------------------
# File reading: CSV/XLSX -> a raw grid of cells (no header assumed yet)
# --------------------------------------------------------------------------


# Sheet names known to hold the actual holdings table in multi-sheet
# workbooks (tried first, in order, as an exact stripped match). If a
# workbook's first/default sheet is a cover or summary sheet instead, this
# is how we find the real one -- add more names here as new providers turn
# up.
HOLDINGS_SHEET_NAMES = ["保有明細"]


def _resolve_holdings_sheet(xls: pd.ExcelFile, path: Path) -> str:
    """Pick which sheet in a multi-sheet workbook actually holds the
    holdings table. Tries HOLDINGS_SHEET_NAMES first (exact match); if none
    of those exist in this workbook, falls back to whichever sheet actually
    contains a row find_header_row() recognizes. Defaults to the first
    sheet if nothing matches -- extraction will then correctly report "no
    recognizable holdings header found" rather than silently reading the
    wrong sheet.
    """
    for candidate in HOLDINGS_SHEET_NAMES:
        for name in xls.sheet_names:
            if str(name).strip() == candidate:
                return name

    for name in xls.sheet_names:
        preview = pd.read_excel(path, sheet_name=name, header=None, nrows=50)
        if find_header_row(preview) is not None:
            return name

    return xls.sheet_names[0]


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
        xls = pd.ExcelFile(path)
        sheet_name = _resolve_holdings_sheet(xls, path)
        return pd.read_excel(path, sheet_name=sheet_name, header=None)

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
# Region detection: ISIN country prefix > Location column > Exchange name > UNKNOWN
# --------------------------------------------------------------------------

# Country/location names -> region code, matched as a substring against a
# Location/Country column when the holdings file has one.
LOCATION_TO_REGION: Dict[str, str] = {
    "JAPAN": "JP", "UNITED STATES": "US", "USA": "US", "U.S.": "US",
    "CHINA": "CN", "HONG KONG": "HK", "TAIWAN": "TW",
    "SOUTH KOREA": "KR", "KOREA": "KR", "INDIA": "IN",
    "UNITED KINGDOM": "GB", "BRITAIN": "GB",
    "GERMANY": "DE", "FRANCE": "FR", "NETHERLANDS": "NL", "ITALY": "IT",
    "SPAIN": "ES", "SWITZERLAND": "CH", "SWEDEN": "SE", "NORWAY": "NO",
    "DENMARK": "DK", "FINLAND": "FI", "BELGIUM": "BE", "AUSTRIA": "AT",
    "POLAND": "PL", "CANADA": "CA", "AUSTRALIA": "AU", "SINGAPORE": "SG",
    "BRAZIL": "BR", "MEXICO": "MX", "ISRAEL": "IL", "SOUTH AFRICA": "ZA",
    "THAILAND": "TH", "INDONESIA": "ID", "MALAYSIA": "MY",
    "PHILIPPINES": "PH", "VIETNAM": "VN",
}

# Exchange name substrings -> region code, used when ISIN and Location
# aren't available/informative. Extend this as new exchanges turn up --
# it's a best-effort heuristic, not an exhaustive list of world exchanges.
EXCHANGE_TO_REGION: Dict[str, str] = {
    "TOKYO": "JP", "OSAKA": "JP", "NAGOYA": "JP",
    "NEW YORK": "US", "NASDAQ": "US", "NYSE": "US", "AMEX": "US", "CBOE": "US",
    "LONDON": "GB", "LSE": "GB",
    "SHENZHEN": "CN", "SHANGHAI": "CN",
    "HONG KONG": "HK", "HKEX": "HK",
    "TAIWAN": "TW", "TAIPEI": "TW",
    "KOREA": "KR", "KOSPI": "KR", "KOSDAQ": "KR",
    "NATIONAL STOCK EXCHANGE OF INDIA": "IN", "BOMBAY": "IN", "BSE": "IN", "NSE": "IN",
    "SINGAPORE": "SG", "SGX": "SG",
    "AUSTRALIA": "AU", "ASX": "AU",
    "TORONTO": "CA", "TSX": "CA",
    "FRANKFURT": "DE", "XETRA": "DE", "DEUTSCHE": "DE",
    "EURONEXT PARIS": "FR", "PARIS": "FR",
    "EURONEXT AMSTERDAM": "NL", "AMSTERDAM": "NL",
    "BORSA ITALIANA": "IT", "MILAN": "IT",
    "MADRID": "ES", "BME": "ES",
    "SIX": "CH", "SWISS": "CH", "ZURICH": "CH",
    "STOCKHOLM": "SE", "OSLO": "NO", "COPENHAGEN": "DK", "HELSINKI": "FI",
    "BRUSSELS": "BE", "VIENNA": "AT", "WARSAW": "PL",
    "JOHANNESBURG": "ZA", "SAO PAULO": "BR", "B3": "BR", "MEXICO": "MX",
    "TEL AVIV": "IL", "BANGKOK": "TH", "JAKARTA": "ID",
    "KUALA LUMPUR": "MY", "MANILA": "PH", "HO CHI MINH": "VN",
}

UNKNOWN_REGION = "UNKNOWN"


def _match_region(text: str, mapping: Dict[str, str]) -> Optional[str]:
    text_upper = text.upper()
    for keyword, code in mapping.items():
        if keyword in text_upper:
            return code
    return None


def get_region(isin: str, location: str, exchange: str) -> str:
    """Work out which region a holding belongs to, in priority order:
    ISIN's 2-letter ISO 6166 country prefix (most reliable when present),
    then a Location/Country column, then the Exchange name. Returns
    UNKNOWN_REGION rather than raising if none of these resolve it -- the
    row still gets written out, just to an UNKNOWN.csv file for review."""
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
# Per-file extraction
# --------------------------------------------------------------------------


class FileResult:
    """Outcome of processing one holdings file, used to build the
    end-of-run summary instead of logging a line per file."""

    def __init__(
        self,
        records: List[dict],
        skipped_no_ticker: int = 0,
        non_stock: Optional[List[dict]] = None,
        problem: Optional[str] = None,
    ):
        self.records = records
        self.skipped_no_ticker = skipped_no_ticker
        self.non_stock = non_stock or []  # e.g. treasury bills, cash, futures
        # None if the file was processed normally (whether or not some
        # individual rows were skipped/filtered). Otherwise a short
        # human-readable reason the file couldn't be processed at all.
        self.problem = problem


def extract_stocks(path: Path) -> FileResult:
    """Return a FileResult with the {Ticker, Name, ISIN, Exchange, Currency,
    Region} dicts found in `path`, one per holding row identified as an
    actual stock. Ticker is required -- a row with an ISIN but no Ticker is
    dropped, since a ticker is what's actually needed to look the stock up
    or trade it. A column missing from the file is left blank ("") for
    every row; a single row missing just one value (other than Ticker) is
    still kept, with only that value blank. Rows blank across every field
    (leftover blank/footer lines) are dropped. Rows that look like
    non-equity instruments (bonds, cash, derivatives -- see
    NON_STOCK_NAME_KEYWORDS) are pulled out separately rather than counted
    as stocks."""
    try:
        raw = read_raw_grid(path)
    except Exception as exc:
        return FileResult([], problem=f"could not be read ({exc})")

    header_row = find_header_row(raw)
    if header_row is None:
        return FileResult([], problem="no recognizable holdings header found")

    df = raw.iloc[header_row + 1 :].copy()
    df.columns = raw.iloc[header_row].tolist()
    df = df.reset_index(drop=True).dropna(how="all")

    col_for_field = {field: find_column(df.columns, field) for field in OUTPUT_FIELDS}
    if all(col is None for col in col_for_field.values()):
        return FileResult([], problem=f"none of {', '.join(OUTPUT_FIELDS)} found")
    location_col = find_column(df.columns, "Location")  # region signal only, not an output field

    records = []
    non_stock = []
    skipped_no_ticker = 0
    for _, row in df.iterrows():
        record = {}
        for field in OUTPUT_FIELDS:
            col = col_for_field[field]
            record[field] = NORMALIZERS[field](row[col]) if col is not None else ""

        if not any(record.values()):
            continue  # fully blank row -- not even a name, ignore silently

        if not record["Ticker"]:
            # No ticker -- even with an ISIN, this isn't usable (ISIN alone
            # doesn't tell you the trading symbol). Also catches rows whose
            # only "ticker" was a placeholder like "-".
            skipped_no_ticker += 1
            logger.debug("%s: no Ticker, can't use: %s", path.name, record)
            continue

        if looks_like_non_stock(record["Name"]):
            non_stock.append(record)
            continue

        location = normalize_text(row[location_col]) if location_col is not None else ""
        record["Region"] = get_region(record["ISIN"], location, record["Exchange"])
        records.append(record)

    return FileResult(records, skipped_no_ticker=skipped_no_ticker, non_stock=non_stock)


# --------------------------------------------------------------------------
# Cross-file dedup
# --------------------------------------------------------------------------


# Filename suffixes that just describe the file's source/format rather
# than identifying the ETF -- stripped when building the short label used
# in the run summary (e.g. "586A_brd_data.xlsx" -> "586A").
_LABEL_SUFFIXES = ["_brd_data", "_holdings"]


def short_label(path: Path) -> str:
    stem = path.stem
    for suffix in _LABEL_SUFFIXES:
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return stem


# Name substrings (case-insensitive) that mark a holding as a non-equity
# instrument -- bonds/bills, cash, repos, derivatives, etc. These show up
# in holdings files with a real ISIN and no ticker (bills genuinely don't
# have one), so the "has an identifier" check alone can't tell them apart
# from a stock. Extend this list as new non-equity instrument types turn up.
NON_STOCK_NAME_KEYWORDS = [
    "TREASURY BILL", "TREASURY NOTE", "TREASURY BOND", "T-BILL",
    "REPURCHASE AGREEMENT", "REPO", "CASH", "MONEY MARKET",
    "COMMERCIAL PAPER", "CERTIFICATE OF DEPOSIT", "BANKERS ACCEPTANCE",
    "FUTURES", "FORWARD", "SWAP", "OPTION",
]


def looks_like_non_stock(name: str) -> bool:
    name_upper = name.upper()
    return any(keyword in name_upper for keyword in NON_STOCK_NAME_KEYWORDS)


def dedupe_key(record: dict) -> str:
    """Identify a stock for dedup purposes. ISIN is the most reliable
    globally-unique identifier, so it's preferred when present. Falling
    back to bare Ticker isn't safe on its own -- the same ticker symbol can
    exist in unrelated companies in different countries -- so the fallback
    is (region, ticker) instead, which only merges rows that are both the
    same symbol and the same region."""
    if record["ISIN"]:
        return f"isin:{record['ISIN']}"
    return f"region_ticker:{record['Region']}:{record['Ticker']}"


class RunSummary:
    """Buckets each input file into one category for the end-of-run report,
    keyed by short_label(path) (e.g. '586A') rather than the full filename."""

    def __init__(self):
        self.clean: List[str] = []  # every row was a clean, identifiable stock
        self.partial: List[str] = []  # some rows filtered out, labelled with why
        self.problems: Dict[str, List[str]] = {}  # reason -> labels
        self.region_counts: Dict[str, int] = {}  # region -> unique stock count, set later

    def add(self, path: Path, result: "FileResult") -> None:
        label = short_label(path)
        if result.problem is not None:
            self.problems.setdefault(result.problem, []).append(label)
            return

        notes = []
        if result.skipped_no_ticker:
            notes.append(f"{result.skipped_no_ticker} no ticker")
        if result.non_stock:
            notes.append(f"{len(result.non_stock)} non-stock")

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


def collect_unique_stocks(holdings_dir: Path) -> tuple[List[dict], "RunSummary"]:
    files = sorted(
        p for p in holdings_dir.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".xlsx", ".xls")
    )
    if not files:
        raise FileNotFoundError(f"no holdings files (.csv/.xlsx) found in {holdings_dir.resolve()}")

    summary = RunSummary()
    seen: Dict[str, dict] = {}
    for path in files:
        result = extract_stocks(path)
        summary.add(path, result)
        for record in result.records:
            key = dedupe_key(record)
            if key in seen:
                logger.debug("Duplicate stock skipped: %s (already seen)", record)
                continue
            seen[key] = record

    return list(seen.values()), summary


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    # Only debug-level per-row detail (e.g. exactly which row lacked a
    # ticker) goes through logging now; set to DEBUG if you need that detail.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

    stocks, summary = collect_unique_stocks(HOLDINGS_DIR)

    by_region: Dict[str, List[dict]] = {}
    for stock in stocks:
        by_region.setdefault(stock["Region"], []).append(stock)
    summary.region_counts = {region: len(rows) for region, rows in by_region.items()}

    REGION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_columns = OUTPUT_FIELDS + ["Region"]
    for region, rows in by_region.items():
        rows.sort(key=lambda r: (r["Ticker"], r["ISIN"], r["Name"]))
        out_path = REGION_OUTPUT_DIR / f"{region}.csv"
        pd.DataFrame(rows, columns=csv_columns).to_csv(out_path, index=False)

    summary.print()
    print(f"Found {len(stocks)} unique stocks across the holdings files in {HOLDINGS_DIR.resolve()}.")
    print(f"Written to {len(by_region)} region file(s) in {REGION_OUTPUT_DIR.resolve()}")
    if UNKNOWN_REGION in by_region:
        print(
            f"Note: {len(by_region[UNKNOWN_REGION])} stock(s) couldn't be placed in a region "
            f"(no usable ISIN prefix, Location, or recognized Exchange) -- see {UNKNOWN_REGION}.csv. "
            f"You may need to add their exchange to EXCHANGE_TO_REGION."
        )


if __name__ == "__main__":
    main()
