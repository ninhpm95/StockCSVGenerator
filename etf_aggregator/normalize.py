from __future__ import annotations

import re

import pandas as pd


_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,5}([.\-][A-Z0-9]{1,4})?$")

def is_valid_ticker(ticker: str) -> bool:
    """Check if string matches a standard stock ticker format
    (1-5 alphanumeric chars, optional dot/dash extension like BRK.B or 0700.HK)."""
    if not isinstance(ticker, str):
        return False
    return bool(_TICKER_PATTERN.match(ticker.strip().upper()))


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
