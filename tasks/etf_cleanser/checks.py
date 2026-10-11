"""Sanity checks on a finished table (used by step 130). They never change
anything: each check returns a list of human-readable lines describing rows
that look unlike the rest, for manual review (check_weights also emits one
file-level line for the weight total). An empty list = nothing odd."""
from __future__ import annotations

import re
from pathlib import Path

from .config import (CHECK_MAX_LISTED, EXCHANGES_PATH, EXCHANGE_UNKNOWN_MAX_LEN,
                    WEIGHT_SUM_EXPECTED, WEIGHT_SUM_EXPECTED_TOLERANCE,
                    WEIGHT_SUM_MAX, WEIGHT_SUM_MIN, WEIGHT_TOTAL_EXCLUDE_NAMES)
from .models import Row, format_weight, parse_float
from .lookup import get_exchange_names
from .instruments import instrument_kind, needs_listing_lookup

def check_tickers(rows: list[Row]) -> list[str]:
    """Validate known market formats without comparing unrelated exchanges."""
    patterns = {"TSE": r"[0-9A-Z]{4}", "HKEX": r"[0-9]{1,5}"}
    odd = []
    for i, row in enumerate(rows):
        if instrument_kind(row) != "security" or not row.code:
            continue
        exchange = (row.exchange or "").upper()
        pattern = patterns.get(exchange)
        if pattern and not re.fullmatch(pattern, row.code.strip().lstrip("'").upper()):
            odd.append(f"line {_line_no(i)}: {row.code} ({exchange})")
    return _report("ticker format inconsistent with exchange", odd)


def check_isins(rows: list[Row]) -> list[str]:
    """Validate identifiers themselves; country diversity is legitimate."""
    odd = [f"line {_line_no(i)}: {r.isin}" for i, r in enumerate(rows)
           if needs_listing_lookup(r) and r.isin and not valid_isin(r.isin)]
    return _report("invalid ISIN format or checksum", odd)


def valid_isin(value: str) -> bool:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in value)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        number = int(ch) * (2 if i % 2 else 1)
        total += number // 10 + number % 10
    return total % 10 == 0


def check_exchanges(rows: list[Row], exchanges_path: Path = EXCHANGES_PATH) -> list[str]:
    """Accept canonical names of any length; flag long unrecognized values."""
    canonical = {name.casefold() for name in get_exchange_names(str(exchanges_path)).values()}
    odd = [f"line {_line_no(i)}: {r.exchange}" for i, r in enumerate(rows)
           if r.exchange and len(r.exchange.strip()) > EXCHANGE_UNKNOWN_MAX_LEN
           and r.exchange.strip().casefold() not in canonical]
    return _report(f"exchange name not unified (unrecognized, over {EXCHANGE_UNKNOWN_MAX_LEN} chars)", odd)


def check_weights(rows: list[Row], stem: str | None = None) -> list[str]:
    """Weights are already percentages (20 means 20%). Flags the file if they
    add up to more than WEIGHT_SUM_MAX or to less than WEIGHT_SUM_MIN (holdings
    probably incomplete), and every row with no usable weight (reported
    separately so a gap isn't mistaken for a low total).
    The total leaves out rows whose Name is in WEIGHT_TOTAL_EXCLUDE_NAMES (currency hedges).
    Files whose stem is in WEIGHT_SUM_EXPECTED (e.g. 2x leveraged funds) are
    judged against expected +/- WEIGHT_SUM_EXPECTED_TOLERANCE instead."""
    parsed = [(i, parse_float(r.weight)) for i, r in enumerate(rows)]
    missing = [f"line {_line_no(i)}: {_label(rows[i])}" for i, w in parsed if w is None]
    weights = [(i, w) for i, w in parsed if w is not None]
    out: list[str] = _report("no usable weight", missing)
    if not weights:
        return out
    total = sum(w for i, w in weights if rows[i].name not in WEIGHT_TOTAL_EXCLUDE_NAMES)

    if stem in WEIGHT_SUM_EXPECTED:
        expected = WEIGHT_SUM_EXPECTED[stem]
        low, high = expected - WEIGHT_SUM_EXPECTED_TOLERANCE, expected + WEIGHT_SUM_EXPECTED_TOLERANCE
        note = f" (expected about {expected:g}% for this file)"
    else:
        low, high, note = WEIGHT_SUM_MIN, WEIGHT_SUM_MAX, ""

    if total > high:
        out.append(f"weight total: {format_weight(total)}% is over {high:g}%{note}")
    elif total < low:
        suffix = f" (over {len(weights)} of {len(rows)} rows)" if missing else ""
        out.append(f"weight total: {format_weight(total)}% is below {low:g}%{note}{suffix}")
    return out


def _label(row: Row) -> str:
    return row.code or row.isin or row.name or "?"


# ---------------------------------------------------------------- helpers --
def _line_no(i: int) -> int:
    """Row index -> line number in the output CSV (header = line 1). Valid
    because step 130 runs on the final table in output order after sorting."""
    return i + 2


def _report(title: str, odd: list[str]) -> list[str]:
    if not odd:
        return []
    lines = [f"{title}: {len(odd)} row(s)"]
    lines += [f"    {line}" for line in odd[:CHECK_MAX_LISTED]]
    if len(odd) > CHECK_MAX_LISTED:
        lines.append(f"    ... and {len(odd) - CHECK_MAX_LISTED} more")
    return lines
