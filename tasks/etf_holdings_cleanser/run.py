"""
Cleanses and normalizes ETF holding files (.xlsx, .csv) from ../../data/ETFs
and writes standardized .csv files into ./cleansed_ETFs.
"""

from pathlib import Path
from collections import defaultdict
import shutil
import sys
import csv

import pandas as pd
import numpy as np

from .constants import *


def is_real_isin(isin_display: str) -> bool:
    if not isin_display:
        return False
    return bool(ISIN_PATTERN.match(str(isin_display).strip().upper()))

# --------------------------------------------------------------------------
# Small helpers needed before reference data is loaded
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg)


# --------------------------------------------------------------------------
# Per-ETF issue report
#
# Instead of printing diagnostics the moment they happen (which produces a
# huge interleaved wall of text), every problem found for a given ETF
# ticker is collected here and printed once, grouped by ticker, at the end
# of the run. See print_report().
# --------------------------------------------------------------------------

ALL_TICKERS: set = set()
ETF_ISSUES: dict = defaultdict(list)
WRITTEN_TICKERS: dict = defaultdict(list)  # ticker -> list of source filenames


def note_ticker_seen(ticker: str) -> None:
    ALL_TICKERS.add(ticker)


def record_issue(ticker: str, issue: str, category: str = "other") -> None:
    """
    category is one of:
      - "not_in_etf_list": ticker missing entirely from etf_list.csv
      - "no_ticker": one or more securities in the holdings couldn't be
        matched to a ticker in stock_list.csv (common for bonds/T-bills,
        cash sweeps that slipped through, etc.)
      - "other": anything that actually blocked/degraded processing
        (header not found, sheet not found, duplicate source files,
        unexpected errors, a blank individual metadata field)
    """
    ALL_TICKERS.add(ticker)
    ETF_ISSUES[ticker].append((category, issue))


def classify_ticker(ticker: str) -> str:
    """
    A ticker can accumulate issues in more than one category (e.g. it's
    missing from etf_list.csv AND has unmatched securities). "other" is
    the only category that reflects an actual processing problem, so it
    always wins the bucket assignment; the two "expected/common" metadata
    categories are informational, not failures.
    """
    cats = {c for c, _ in ETF_ISSUES.get(ticker, [])}

    if "other" in cats:
        return "other"
    if "not_in_etf_list" in cats:
        return "not_in_etf_list"
    if "no_ticker" in cats:
        return "no_ticker"
    return "success"


def print_report() -> None:
    buckets = defaultdict(list)
    for ticker in ALL_TICKERS:
        buckets[classify_ticker(ticker)].append(ticker)

    def csv_line(label, key):
        tickers = sorted(buckets[key])
        return f"{label}: " + (", ".join(tickers) if tickers else "(none)")

    lines = []
    lines.append(csv_line("Successful ETFs", "success"))
    lines.append("")
    lines.append(csv_line("ETFs not found in ETF list", "not_in_etf_list"))
    lines.append("")
    lines.append(csv_line("ETFs with no ticker", "no_ticker"))
    lines.append("")
    lines.append("Others:")

    others = sorted(buckets["other"])
    if not others:
        lines.append("(none)")

    for ticker in others:
        lines.append(f"{ticker}:")
        lines.append("")
        for _, issue in ETF_ISSUES[ticker]:
            lines.append(f"* {issue}")
        lines.append("")

    print("\n".join(lines).rstrip())


# --------------------------------------------------------------------------
# Reference data loaders
# --------------------------------------------------------------------------

def load_security_lookup(filepath: Path):
    """
    Load the stock reference list used to impute a missing Code/Ticker via
    exact ISIN or exact Name match.

    Expected columns: Ticker, Name, ISIN, Region
    Returns a list of {"isin": ..., "name": ..., "code": ...} dicts, where
    "code" is taken from the Ticker column.
    """
    if not filepath.exists():
        log(f"[WARN] Stock list not found at {filepath}; SECURITY_LOOKUP will be empty.")
        return []

    df = pd.read_csv(filepath, dtype=str, keep_default_na=False)

    lookup = []
    for _, row in df.iterrows():
        lookup.append({
            "isin": row.get("ISIN", ""),
            "name": row.get("Name", ""),
            "code": row.get("Ticker", ""),
        })

    return lookup


def load_etf_metadata(filepath: Path):
    """
    Load ETF reference metadata used to build output filenames.

    Expected columns: Ticker, Name, Short Name, Region, Company
    Returns a dict: {ticker: {"name": ..., "short_name": ..., "region": ..., "company": ...}}
    """
    if not filepath.exists():
        log(f"[WARN] ETF list not found at {filepath}; ETF_METADATA will be empty.")
        return {}

    df = pd.read_csv(filepath, dtype=str, keep_default_na=False)

    metadata = {}
    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip()

        if not ticker:
            continue

        metadata[ticker] = {
            "name": row.get("Name", ""),
            "short_name": row.get("Short name", ""),
            "region": row.get("Region", ""),
            "company": row.get("Company", ""),
        }

    return metadata


SECURITY_LOOKUP = load_security_lookup(STOCK_LIST_CSV)
ETF_METADATA = load_etf_metadata(ETF_LIST_CSV)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def extract_filename_ticker(filename: str) -> str:
    """1655_2024.xlsx -> '1655'"""
    stem = Path(filename).stem
    return stem.split("_")[0]


def build_lookup_indices(lookup):
    """Build ISIN->code and Name->code dicts (normalized, uppercased, stripped)."""
    isin_map = {}
    name_map = {}

    for entry in lookup:
        code = entry.get("code")
        if not code:
            continue

        isin = entry.get("isin")
        name = entry.get("name")

        if isin:
            isin_map[str(isin).strip().upper()] = code

        if name:
            name_map[str(name).strip().upper()] = code

    return isin_map, name_map


ISIN_MAP, NAME_MAP = build_lookup_indices(SECURITY_LOOKUP)


def read_stitched_csv(filepath: Path) -> pd.DataFrame:
    """
    Read CSV files where different sections/tables have different numbers
    of columns.

    Normal pd.read_csv() expects every row to have the same number of fields.
    Some ETF files instead contain a small metadata table followed by a
    wider holdings table. csv.reader() lets us read all rows regardless of
    their width. Shorter rows are then padded with None.
    """

    # Try common encodings used by Japanese ETF files.
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp932",
        "shift_jis",
    ]

    last_error = None

    for encoding in encodings:
        try:
            with open(filepath, "r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)

            if not rows:
                return pd.DataFrame()

            # Find the widest row.
            max_columns = max(len(row) for row in rows)

            # Pad all rows to the same width.
            padded_rows = [
                row + [None] * (max_columns - len(row))
                for row in rows
            ]

            return pd.DataFrame(padded_rows, dtype=str)

        except UnicodeDecodeError as e:
            last_error = e
            continue

    raise last_error


def find_truncation_row(df: pd.DataFrame):
    """
    Scan column 0 top-to-bottom for the first cell containing "Fund
    Holdings as of" (case-insensitive substring). Returns the positional
    row index of that match, or None if not found.

    IMPORTANT: this must only be called on data that comes AFTER the
    header row has already been located (see cleanse_dataframe). Several
    source files (e.g. iShares "*_holdings.csv" exports) put "Fund
    Holdings as of <date>" in a title row ABOVE the real header row.
    Truncating on that first mention before finding the header wipes the
    whole sheet to 0 rows.
    """
    if df.empty or df.shape[1] == 0:
        return None

    col0 = df.iloc[:, 0].astype(str)

    mask = col0.str.lower().str.contains(
        TRUNCATE_MARKER,
        na=False,
        regex=False,
    )

    match_positions = np.where(mask.values)[0]

    if len(match_positions) >= 1:
        return int(match_positions[0])

    return None


def find_header_row(df: pd.DataFrame):
    """
    Scan rows top-to-bottom. A row is a header if, for any target keyword
    combination, each keyword is found in a distinct cell of that row AND
    the keywords are found in the same left-to-right order given in the
    combination.

    Case-sensitive matching.
    Returns the positional row index, or None if not found.
    """
    n_rows = min(len(df), HEADER_SCAN_LIMIT)

    for i in range(n_rows):
        row_vals = [
            "" if pd.isna(x) else str(x)
            for x in df.iloc[i].tolist()
        ]

        for target_combo in HEADER_KEYWORD_COMBINATIONS:
            last_matched_idx = -1
            combo_matched = True

            for keyword in target_combo:
                found_idx = None

                # Only look to the right of the previous keyword's match.
                for cell_idx in range(last_matched_idx + 1, len(row_vals)):
                    if keyword in row_vals[cell_idx]:
                        found_idx = cell_idx
                        break

                if found_idx is None:
                    combo_matched = False
                    break

                last_matched_idx = found_idx

            if combo_matched:
                return i

    return None


def find_column(columns, keywords):
    """Return the first column label whose lowercased string contains any keyword."""
    for col in columns:
        col_lower = str(col).lower()

        for kw in keywords:
            if kw.lower() in col_lower:
                return col

    return None


def is_missing(val) -> bool:
    if val is None:
        return True

    if isinstance(val, float) and pd.isna(val):
        return True

    if pd.isna(val):
        return True

    if isinstance(val, str) and val.strip() == "":
        return True

    return False


def is_expected_unmatched(stock_name, isin_display) -> bool:
    """
    True for rows that are never going to match the stock list and whose
    unmatched status is expected, not actionable:
      - fully blank rows (padding rows / spacer rows)
      - fund total / NAV footer rows (SKIP_KEYWORDS)
      - FX cash-sweep lines (ISIN like "CASHUSDJPY01")
      - forward contracts (ISIN literally "FORWARD")
      - index/commodity futures contracts (no ISIN, name ends in a
        month-abbreviation + year, e.g. "TOPIX FUTURES SEP.2026")
    """
    name = "" if stock_name is None else str(stock_name).strip()
    isin = "" if isin_display is None else str(isin_display).strip().upper()

    # Source files sometimes literally contain the text "nan" for a blank
    # name cell (pandas dtype=str coercion upstream of this script).
    if name.lower() == "nan":
        name = ""

    if not name and not isin:
        return True

    if isin.startswith("CASH"):
        return True

    if isin == "FORWARD":
        return True

    if name and any(kw.upper() in name.upper() for kw in SKIP_KEYWORDS):
        return True

    if not isin and name and FUTURES_DATE_PATTERN.search(name):
        return True

    return False


def impute_missing_codes(
    df: pd.DataFrame,
    code_col,
    name_col,
    isin_col,
    etf_ticker: str,
):
    """
    For rows with a missing code, try ISIN match then Name match against
    SECURITY_LOOKUP. Logs unmatched rows to the terminal.
    """

    if code_col is None:
        return df

    unmatched_with_isin = []  # real security, just missing from stock_list.csv
    unmatched_no_isin = []    # no identifiable ISIN (bonds, T-bills, etc.)

    for idx in df.index:
        code_val = df.at[idx, code_col]

        if not is_missing(code_val):
            continue

        isin_val = (
            df.at[idx, isin_col]
            if isin_col is not None
            else None
        )

        name_val = (
            df.at[idx, name_col]
            if name_col is not None
            else None
        )

        resolved_code = None

        # First try exact ISIN match.
        if not is_missing(isin_val):
            resolved_code = ISIN_MAP.get(
                str(isin_val).strip().upper()
            )

        # Then try exact security-name match.
        if resolved_code is None and not is_missing(name_val):
            resolved_code = NAME_MAP.get(
                str(name_val).strip().upper()
            )

        if resolved_code is not None:
            df.at[idx, code_col] = resolved_code

        else:
            stock_name = (
                ""
                if is_missing(name_val)
                else name_val
            )

            isin_display = (
                ""
                if is_missing(isin_val)
                else isin_val
            )

            if is_expected_unmatched(stock_name, isin_display):
                continue

            label = stock_name if stock_name else "(unnamed)"

            if is_real_isin(isin_display):
                unmatched_with_isin.append(f"{label} ({isin_display})")
            else:
                unmatched_no_isin.append(label)

    if unmatched_with_isin:
        record_issue(
            etf_ticker,
            "These securities have a valid ISIN but are missing from "
            "stock_list.csv - add them there to get a ticker matched: "
            + ", ".join(unmatched_with_isin),
            category="other",
        )

    if unmatched_no_isin:
        record_issue(
            etf_ticker,
            "These holdings have no ISIN and couldn't be matched to a "
            "ticker (typically bonds, T-bills, or similar non-equity "
            "instruments): " + ", ".join(unmatched_no_isin),
            category="no_ticker",
        )

    return df


def apply_truncation(df: pd.DataFrame) -> pd.DataFrame:
    cutoff = find_truncation_row(df)

    if cutoff is not None:
        df = df.iloc[:cutoff].reset_index(drop=True)

    return df


def clean_header_cell(value) -> str:
    """
    Some source files encode embedded line breaks as the literal escape
    sequences "_x000D_" / "_x000A_" instead of real CR/LF characters
    (e.g. "銘柄コード_x000D_ （Code）"). Strip those, along with actual
    \\r/\\n characters, and collapse whitespace so headers stay readable
    and match the CODE/NAME/ISIN keyword lookups.
    """
    text = str(value)

    text = text.replace("_x000D_", " ").replace("_x000A_", " ")
    text = text.replace("\r", " ").replace("\n", " ")

    return " ".join(text.split()).strip()


class HeaderNotFoundError(Exception):
    """
    Raised when no header row could be detected in a file. Callers should
    catch this and fall back to copying the source file as-is rather than
    guessing at a header row, so the file can be investigated manually.
    """


def apply_header(df: pd.DataFrame, filename: str):
    """
    Locate the header row and split the sheet into:
      - metadata_rows: whatever preceded the header row (title/metadata
        rows we want to preserve, not discard).
      - data: the header-labeled table below it.

    Raises HeaderNotFoundError if no header row could be detected, rather
    than silently falling back to row 0.
    """
    header_idx = find_header_row(df)

    if header_idx is None:
        raise HeaderNotFoundError(
            f"Could not detect header row in {filename}"
        )

    # Preserve whatever came before the header row (e.g. title/metadata
    # rows) so it can be reattached later instead of being discarded.
    metadata_rows = df.iloc[:header_idx].reset_index(drop=True)

    new_header = df.iloc[header_idx]

    data = df.iloc[header_idx + 1:].reset_index(drop=True)

    # Convert header cells to strings and strip stray escape artifacts.
    header_labels = [clean_header_cell(c) for c in new_header]
    data.columns = header_labels

    # Metadata rows share the same column positions as the data, so give
    # them the same labels for a clean concat later on.
    metadata_rows.columns = header_labels

    return data, metadata_rows


def cleanse_dataframe(
    df: pd.DataFrame,
    filename: str,
    etf_ticker: str,
) -> pd.DataFrame:

    # 1. Find the actual holdings table header FIRST. Some source files
    #    (e.g. iShares "*_holdings.csv") have "Fund Holdings as of <date>"
    #    as a title row ABOVE the header, so truncation must not run
    #    before the header is located, or the sheet gets wiped to 0 rows.
    #    Rows above the header (title/metadata rows) are preserved here
    #    and reattached at the end instead of being discarded.
    df, metadata_rows = apply_header(df, filename)

    # 2. Now that we're looking only at data below the header, remove
    #    anything from the "Fund Holdings as of" marker downwards, if
    #    present.
    df = apply_truncation(df)

    # 3. Identify important columns.
    code_col = find_column(
        df.columns,
        CODE_COLUMN_KEYWORDS,
    )

    name_col = find_column(
        df.columns,
        NAME_COLUMN_KEYWORDS,
    )

    isin_col = find_column(
        df.columns,
        ISIN_COLUMN_KEYWORDS,
    )

    # 4. Impute missing codes from ISIN/name lookup.
    df = impute_missing_codes(
        df,
        code_col,
        name_col,
        isin_col,
        etf_ticker,
    )

    # 5. Reattach the preserved metadata/title rows at the end of the
    #    output, instead of discarding them.
    if not metadata_rows.empty:
        df = pd.concat([df, metadata_rows], ignore_index=True)

    return df


def resolve_output_name(filename_ticker: str) -> str:
    """
    Build the output filename stem as: ticker_region_company_shortName
    Any field not found in ETF_METADATA (or blank) is filled with "NA",
    and the gap is recorded as an issue for the final report.
    e.g. 1655_US_ishares_sp500  (or 1655_NA_ishares_NA if some fields
    can't be resolved).
    """
    meta = ETF_METADATA.get(filename_ticker)

    if meta is None:
        record_issue(
            filename_ticker,
            "ETF ticker not found in etf_list.csv; output filename fields "
            "default to 'NA'.",
            category="not_in_etf_list",
        )
        meta = {}

    def field_or_na(key):
        value = meta.get(key, "")
        value = "" if value is None else str(value).strip()
        return value if value else "NA"

    region = field_or_na("region")
    company = field_or_na("company")
    short_name = field_or_na("short_name")

    return f"{filename_ticker}_{region}_{company}_{short_name}"


def save_output(df: pd.DataFrame, filename_ticker: str) -> Path:
    out_name = resolve_output_name(filename_ticker) + ".csv"

    out_path = OUTPUT_DIR / out_name

    df.to_csv(
        out_path,
        index=False,
        encoding="utf-8-sig",
    )

    return out_path


# --------------------------------------------------------------------------
# Per-file processing
# --------------------------------------------------------------------------

def copy_source_as_is(filepath: Path) -> None:
    """
    Copy the original source file into OUTPUT_DIR untouched, e.g. when we
    can't confidently locate a header row / sheet and don't want to guess.
    """
    dest = OUTPUT_DIR / filepath.name
    shutil.copy2(filepath, dest)


def find_target_sheet(filepath: Path):
    """
    Decide which sheet in an .xlsx file holds the holdings table.

    1. If any sheet matching XLSX_SHEET_NAMES exists, use the first match.
    2. Otherwise, scan every sheet (top rows only) for a recognizable
       header row using the same left-to-right keyword logic as
       find_header_row, and use the first sheet that matches.

    Returns the chosen sheet name, or None if neither approach found one.
    """
    xl = pd.ExcelFile(filepath, engine="openpyxl")
    sheet_names = xl.sheet_names

    # Check for exact sheet name matches in order of priority
    for name in XLSX_SHEET_NAMES:
        if name in sheet_names:
            return name

    # Fallback: scan sheet content for a valid header row
    for sheet_name in sheet_names:
        sheet_df = xl.parse(sheet_name, header=None, dtype=str)

        if find_header_row(sheet_df) is not None:
            return sheet_name

    return None


def process_csv(filepath: Path):
    ticker = extract_filename_ticker(filepath.name)
    note_ticker_seen(ticker)

    # IMPORTANT:
    # Do NOT use pd.read_csv() here because some source files contain
    # multiple stitched tables with different column counts.
    df = read_stitched_csv(filepath)

    try:
        df = cleanse_dataframe(
            df,
            filepath.name,
            ticker,
        )

    except HeaderNotFoundError:
        record_issue(
            ticker,
            f"Header row not found in {filepath.name}; file copied as-is "
            "for manual review.",
        )
        copy_source_as_is(filepath)
        return

    out_path = save_output(
        df,
        ticker,
    )
    WRITTEN_TICKERS[ticker].append(filepath.name)


def process_xlsx(filepath: Path):
    ticker = extract_filename_ticker(filepath.name)
    note_ticker_seen(ticker)

    sheet_name = find_target_sheet(filepath)

    if sheet_name is None:
        record_issue(
            ticker,
            f"No sheet named {XLSX_SHEET_NAMES} and no sheet with a "
            f"recognizable header row in {filepath.name}; file copied "
            "as-is for manual review.",
        )
        copy_source_as_is(filepath)
        return

    df = pd.read_excel(
        filepath,
        sheet_name=sheet_name,
        header=None,
        dtype=str,
        engine="openpyxl",
    )

    try:
        df = cleanse_dataframe(
            df,
            filepath.name,
            ticker,
        )

    except HeaderNotFoundError:
        record_issue(
            ticker,
            f"Header row not found in {filepath.name}; file copied as-is "
            "for manual review.",
        )
        copy_source_as_is(filepath)
        return

    out_path = save_output(
        df,
        ticker,
    )
    WRITTEN_TICKERS[ticker].append(filepath.name)


def run():
    if not INPUT_DIR.exists():
        log(
            f"[ERROR] Input directory not found: "
            f"{INPUT_DIR}"
        )
        sys.exit(1)

    # Wipe the output directory on every run. Without this, files from a
    # previous run (e.g. under an old naming scheme, or a raw .xlsx copy
    # from a run where the sheet lookup used to fail) stick around
    # alongside the newly generated file for the same ticker.
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    files = sorted(
        [
            f
            for f in INPUT_DIR.iterdir()
            if f.suffix.lower() in (".csv", ".xlsx")
        ]
    )

    if not files:
        log(
            f"[INFO] No .csv or .xlsx files found in "
            f"{INPUT_DIR}"
        )
        return

    for filepath in files:
        try:
            if filepath.suffix.lower() == ".csv":
                process_csv(filepath)

            elif filepath.suffix.lower() == ".xlsx":
                process_xlsx(filepath)

        except Exception as e:
            ticker = extract_filename_ticker(filepath.name)
            record_issue(
                ticker,
                f"Unexpected error while processing {filepath.name}: {e}",
            )

    # Multiple source files for the same ticker silently overwrite each
    # other's output (last one processed wins) - flag it instead of
    # letting it happen invisibly.
    for ticker, sources in WRITTEN_TICKERS.items():
        if len(sources) > 1:
            record_issue(
                ticker,
                "Multiple source files map to this ticker; only the last "
                f"one's output was kept: {', '.join(sources)}",
            )

    print()
    print_report()
    log(
        f"\nDone. Output written to: "
        f"{OUTPUT_DIR}"
    )
