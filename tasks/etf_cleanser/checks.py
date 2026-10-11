"""Sanity checks on a finished table (used by step 130). They never change
anything: each check returns a list of human-readable lines describing rows
that look unlike the rest, for manual review (check_weights also emits one
file-level line for the weight total). An empty list = nothing odd."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .config import (CHECK_DOMINANT_SHARE, CHECK_IGNORE_CODES, CHECK_MAX_LISTED,
                    EXCHANGES_PATH, EXCHANGE_UNKNOWN_MAX_LEN, HOLDINGS_MIN_COUNT, ISIN_SKIP_COUNTRIES,
                    WEIGHT_SUM_EXPECTED, WEIGHT_SUM_EXPECTED_TOLERANCE,
                    WEIGHT_SUM_MAX, WEIGHT_SUM_MIN, WEIGHT_TOTAL_EXCLUDE_NAMES)
from .models import Row, format_weight, parse_float
from .lookup import get_exchange_names

_SEPARATORS = re.compile(r"[^A-Za-z0-9]")


def check_tickers(rows: list[Row]) -> list[str]:
    """Only runs with at least HOLDINGS_MIN_COUNT checkable tickers (separators
    like . and - are ignored when measuring; cash codes and, in ISIN-bearing
    files, rows without an ISIN are skipped, see _checkable).
    Kind: if most tickers are text style (at least 2 letters: AAPL, BRK.B,
    B3SA3, BPAC11, 0I9F), list the ones that aren't. If most are digit style
    (digits with at most one letter: 7021, 586A), list the ones that aren't,
    and also check the length: if one length dominates (JP: 4 chars), list
    the digit-style tickers with any other length. The length check is skipped
    for text style, where lengths legitimately vary."""
    coded = [(i, r.code, _SEPARATORS.sub("", r.code)) for i, r in _checkable(rows) if r.code]
    coded = [(i, raw, core) for i, raw, core in coded if core]
    if len(coded) < HOLDINGS_MIN_COUNT:
        return []
    out: list[str] = []

    n_text = sum(_is_text(core) for _, _, core in coded)
    n_jp = sum(_is_jp_style(core) for _, _, core in coded)
    if _mostly(n_text, len(coded)):
        odd = [f"line {_line_no(i)}: {raw}" for i, raw, core in coded if not _is_text(core)]
        out += _report(f"Ticker kind: {_pct(n_text, len(coded))} of tickers are text "
                       f"(2+ letters), these are not", odd)
    elif _mostly(n_jp, len(coded)):
        odd = [f"line {_line_no(i)}: {raw}" for i, raw, core in coded if not _is_jp_style(core)]
        out += _report(f"Ticker kind: {_pct(n_jp, len(coded))} of tickers are digits with "
                       f"at most one letter, these are not", odd)
        # Length only matters for fixed-width numeric styles (JP: 4 chars).
        top_len, top_n = Counter(len(core) for _, _, core in coded).most_common(1)[0]
        if _mostly(top_n, len(coded)):
            odd = [f"line {_line_no(i)}: {raw} ({len(core)} chars)"
                   for i, raw, core in coded if len(core) != top_len]
            out += _report(f"ticker length: {_pct(top_n, len(coded))} of tickers are "
                           f"{top_len} chars, these are not", odd)
    return out


def check_isins(rows: list[Row]) -> list[str]:
    """Most ISINs share a country prefix (e.g. JP), a few have another.
    Skipped when the dominant prefix is in ISIN_SKIP_COUNTRIES (US ETFs hold many foreign ISINs).
    Only runs with at least HOLDINGS_MIN_COUNT ISINs."""
    isins = [(i, r.isin.strip()) for i, r in enumerate(rows) if r.isin and len(r.isin.strip()) >= 2]
    if len(isins) < HOLDINGS_MIN_COUNT:
        return []
    prefixes = Counter(v[:2].upper() for _, v in isins)
    top, top_n = prefixes.most_common(1)[0]
    if top in ISIN_SKIP_COUNTRIES or not _mostly(top_n, len(isins)):
        return []
    odd = [f"line {_line_no(i)}: {v} ({v[:2].upper()})" for i, v in isins if v[:2].upper() != top]
    return _report(f"ISIN country: {_pct(top_n, len(isins))} of ISINs start with '{top}', "
                   f"these don't", odd)


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
    because step 130 runs on the final table in output order after sorting
    and step 120's write."""
    return i + 2


def _checkable(rows: list[Row]) -> list[tuple[int, Row]]:
    """(index, row) pairs worth checking: skips CHECK_IGNORE_CODES and, when most
    rows carry an ISIN, rows without one (cash, margin, futures). The original
    index is kept so _line_no still works."""
    isin_share = sum(bool(r.isin) for r in rows) / len(rows) if rows else 0
    require_isin = isin_share >= CHECK_DOMINANT_SHARE
    return [(i, r) for i, r in enumerate(rows)
            if (r.code or "").strip().upper() not in CHECK_IGNORE_CODES
            and not (require_isin and not r.isin)]


def _is_text(core: str) -> bool:
    """Text style: at least 2 letters (AAPL, B3SA3, BPAC11, 0I9F), or a lone letter
    with no digits (F, T). Never overlaps _is_jp_style."""
    letters = sum(ch.isalpha() for ch in core)
    digits = len(core) - letters
    return letters >= 2 or (letters == 1 and digits == 0)


def _is_jp_style(core: str) -> bool:
    """Digits with at most one letter: 7021, 586A, 00388."""
    letters = sum(ch.isalpha() for ch in core)
    return letters <= 1 and any(ch.isdigit() for ch in core)


def _mostly(n: int, total: int) -> bool:
    """At least CHECK_DOMINANT_SHARE of the rows, but not all of them."""
    return total > 0 and n < total and n / total >= CHECK_DOMINANT_SHARE


def _pct(n: int, total: int) -> str:
    return f"{n / total:.1%} ({n}/{total})"


def _report(title: str, odd: list[str]) -> list[str]:
    if not odd:
        return []
    lines = [f"{title}: {len(odd)} row(s)"]
    lines += [f"    {line}" for line in odd[:CHECK_MAX_LISTED]]
    if len(odd) > CHECK_MAX_LISTED:
        lines.append(f"    ... and {len(odd) - CHECK_MAX_LISTED} more")
    return lines
