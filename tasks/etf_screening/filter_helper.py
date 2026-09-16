import re
import pandas as pd
from .constants import Fields as F

def normalize_ticker(t) -> str:
    """Normalize a ticker value to a stripped string.

    NaN/missing values become "" rather than the literal string "nan". If we
    let NaN become "nan", every row with a missing ticker collapses onto the
    same fake key, which can cause spurious many-to-one merges (e.g. against
    current_df) and corrupt or duplicate rows in the output.
    """
    if pd.isna(t):
        return ""
    return str(t).strip()


def parse_number(raw):
    """Convert fee-like values to a canonical fraction, e.g. 0.15% -> 0.0015.

    Handles plain numbers ("0.15"), percent-suffixed numbers ("0.15%",
    "1.26%"), a leading quote some spreadsheet exports add ("'0.09%"), and
    footnote markers like "(*1)". Returns None if the value can't be parsed.

    ASSUMPTION (please verify against your real data): bare, non-percent
    numbers are treated as being on the same percent scale as the
    percent-suffixed ones -- i.e. "0.15" is treated as meaning "0.15%", same
    as an explicit "0.15%", and both are divided by 100. Previously "0.15"
    and "0.15%" parsed to values 100x apart (0.15 vs 0.0015), which would
    silently corrupt any fee comparison (e.g. the "lowest fee wins" sort in
    run.py's group dedup) if the source CSV mixes both forms.

    If bare numbers in your data are actually already-decimal fractions
    (e.g. a bare 0.0015 meaning 0.15%, not a bare 0.15 meaning 0.15%),
    change the return statement below to only divide when has_percent is
    True.
    """
    if pd.isna(raw):
        return None
    s = str(raw).strip().lstrip("'")
    s = re.sub(r"\(\*\d+\)", "", s).strip()
    if s.endswith("%"):
        s = s[:-1]
    try:
        value = float(s)
    except ValueError:
        return None
    return value / 100


def load_lookup_fees(lookup_path) -> dict:
    lookup = pd.read_csv(lookup_path, dtype=str)

    missing = [c for c in (F.TICKER, F.FEE) if c not in lookup.columns]
    if missing:
        raise ValueError(
            f"Lookup CSV at {lookup_path} is missing required column(s): {missing}"
        )

    lookup[F.TICKER] = lookup[F.TICKER].map(normalize_ticker)
    lookup[F.FEE] = lookup[F.FEE].map(parse_number)

    # Drop rows with no usable ticker (now "" instead of "nan" -- see
    # normalize_ticker) before they can collide with each other as a shared
    # fake key.
    lookup = lookup[lookup[F.TICKER] != ""]

    # If a ticker appears more than once, keep the first occurrence
    # (dict(zip(...)) would otherwise silently keep the last one).
    lookup = lookup.drop_duplicates(subset=[F.TICKER], keep="first")
    return dict(zip(lookup[F.TICKER], lookup[F.FEE]))
