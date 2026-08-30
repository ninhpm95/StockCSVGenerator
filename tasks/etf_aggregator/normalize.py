from __future__ import annotations

import re

import pandas as pd


# Codes that show up in the "Code" column of holdings files but aren't
# equities -- cash positions, currency balances, collateral, corporate
# actions, etc. These are legitimate holdings, just not ones that will
# ever be found in a stock database, so they're filtered out rather than
# reported as misses. Add more here as new non-stock codes turn up.
_RESERVED_NON_STOCKS = {
    "CASH", "COLLATERAL", "RIGHTS", "MARGIN", "PENDING", "SUSPENSE",
    "OTHER", "FUTURES", "OPTIONS", "SWAP", "FORWARD", "ACCRUED",
    "N/A", "NA", "TBD", "UNKNOWN",
    "－", "-",
    # currencies
    "USD", "JPY", "EUR", "GBP", "HKD", "AUD", "CNY", "KRW", "TWD",
    "INR", "CAD", "CHF", "SGD", "NZD",
}


def is_reserved_non_stock(code: str) -> bool:
    """Check whether a holding's Code is a known non-stock placeholder
    (cash, currency, collateral, ...) rather than an actual ticker."""
    return normalize_ticker(code) in _RESERVED_NON_STOCKS


_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def is_valid_isin(isin: str) -> bool:
    """Check if a string matches the standard ISIN format: 2-letter country
    code, 9 alphanumeric characters, 1 numeric check digit (12 chars total)."""
    if not isinstance(isin, str):
        return False
    return bool(_ISIN_PATTERN.match(isin.strip().upper()))


def normalize_text(value) -> str:
    """Normalize a general string for matching."""
    if pd.isna(value):
        return ""

    s = str(value).strip()

    # Remove Excel/pasted leading apostrophes and surrounding quotes.
    s = s.lstrip("'")
    s = s.strip().strip('"').strip("'").strip()

    return s


def normalize_ticker(value) -> str:
    """
    Normalize ticker values while preserving meaningful alphanumeric codes.

    Examples:
        1605       -> 1605
        "1605      -> 1605
        '1605'     -> 1605
        1605.0     -> 1605
        "  1605 "  -> 1605
    """
    s = normalize_text(value)

    if not s:
        return ""

    # pandas/Excel can turn numeric tickers into "1605.0".
    if re.fullmatch(r"[+-]?\d+\.0+", s):
        s = s.split(".", 1)[0]

    return s.upper()


_REGION_ALIASES = {
    "JAPAN": "JP",
    "USA": "US",
    "UNITED STATES": "US",
    "HONG KONG": "HK",
    "KOREA": "KR",
    "SOUTH KOREA": "KR",
    "TAIWAN": "TW",
    "CHINA": "CN",
    "UNITED KINGDOM": "GB",
    "UK": "GB",
    "AUSTRALIA": "AU",
    "INDIA": "IN",
}


def normalize_region(value) -> str:
    """Normalize a region/country code."""
    s = normalize_text(value).upper()
    return _REGION_ALIASES.get(s, s)


def normalize_exchange(value) -> str:
    return normalize_text(value).upper()


def normalize_isin(value) -> str:
    return normalize_text(value).upper()
