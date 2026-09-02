from pathlib import Path
from typing import Dict, List, Tuple

# --------------------------------------------------------------------------
# Input/output locations.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent           # parent/tasks/ticker_extractor
REPO_ROOT = PROJECT_ROOT.parent.parent                    # parent
HOLDINGS_DIR = REPO_ROOT / "data" / "ETFs"
LOOKUP_DIR = REPO_ROOT / "data" / "stocks"
OUTPUT_DIR = PROJECT_ROOT / "output"

TOP_N_HOLDINGS = 100

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
    # Region-detection signal only, not written out as its own column.
    "Location": ["Location", "Country", "国", "国名", "所在国", "所在地"],
    # Used to pick the top-N holdings, not written out as its own column.
    "Weight": ["純資産比率 % of NAV", "純資産比率", "% of NAV", "Weight (%)", "Weight"],
    "Price": ["Stock Price", "Price", "株価"],
    "Shares": ["株数（※）No. of Shares（※）", "Shares Amount", "No. of Shares", "株数", "Shares"],
}

# A row is treated as the holdings-table header if it matches one of these
# keyword sequences. Combinations are tried in order, top to bottom, row by
# row; the first row where ANY combination matches wins. Within a
# combination, keywords are matched left to right: once a cell containing
# keyword N (case-sensitive substring) is found, the search for keyword N+1
# resumes from the next cell onward -- so the matched cells must appear in
# order, but don't need to be adjacent.
HEADER_KEYWORD_COMBINATIONS: List[Tuple[str, ...]] = [
    ("銘柄コード", "ISINコード", "Name"),
    ("Code", "Name", "ISIN"),
    ("Code", "Name", "Weight"),
    ("Ticker", "Name", "Weight"),
]


# --------------------------------------------------------------------------
# Text normalization
# --------------------------------------------------------------------------

# Tokens that source files use to mean "no value" rather than leaving the
# cell blank. These normalize to "" so downstream logic isn't fooled into
# treating a placeholder dash as a real value.
PLACEHOLDER_TOKENS = {"-", "--", "―", "‐", "‑", "n/a", "na", "null", "none"}


# --------------------------------------------------------------------------
# File reading: CSV/XLSX -> a raw grid of cells (no header assumed yet)
# --------------------------------------------------------------------------

# Encodings to try, in order, when reading a holdings CSV. Source files come
# from more than one provider (e.g. iShares exports are plain UTF-8, Next
# Funds exports are Shift-JIS/CP932) and don't declare their own encoding,
# so we try each until one decodes cleanly. utf-8-sig also handles plain
# utf-8 (with or without a BOM), so it's tried first.
CSV_ENCODINGS: List[str] = ["utf-8-sig", "cp932"]

# Sheet names known to hold the actual holdings table in multi-sheet
# workbooks (tried first, in order, as an exact stripped match).
HOLDINGS_SHEET_NAMES = ["保有明細"]



# --------------------------------------------------------------------------
# Region detection: ISIN country prefix > Location column > Exchange name > UNKNOWN
# --------------------------------------------------------------------------

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


_LABEL_SUFFIXES = ["_brd_data", "_holdings"]
