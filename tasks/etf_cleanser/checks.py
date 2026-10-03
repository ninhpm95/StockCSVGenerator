"""Sanity checks on a finished table (used by step 100). They never change
anything: each check returns a list of human-readable lines describing rows
that look unlike the rest, for manual review (check_weights also emits one
file-level line for the weight total). An empty list = nothing odd."""
from __future__ import annotations

import re
from collections import Counter

from .config import (CHECK_DOMINANT_SHARE, CHECK_MAX_LISTED, WEIGHT_TOTAL_EXCLUDE_NAMES,
                    EXCHANGE_SHORT_MAX_LEN, HOLDINGS_MIN_COUNT, ISIN_SKIP_COUNTRIES,
                    WEIGHT_SUM_MAX, WEIGHT_SUM_MIN)
from .models import Row, format_weight, parse_float

_SEPARATORS = re.compile(r"[^A-Za-z0-9]")


def check_tickers(rows: list[Row]) -> list[str]:
    """Only runs with at least HOLDINGS_MIN_COUNT tickers (separators like . and -
    are ignored when measuring). Two checks:
    (a) length: find the most common ticker length; if it covers most tickers,
        list the tickers with any other length.
    (b) kind: if most tickers are text (US style: AAPL, BRK.B, JPWU6, 0HQN; at most one digit),
        list the ones that aren't; if most are digits-only or digits plus a single
        letter (JP style: 7021, 586A), list the ones that aren't."""
    coded = [(i, r.code, _SEPARATORS.sub("", r.code)) for i, r in enumerate(rows) if r.code]
    coded = [(i, raw, core) for i, raw, core in coded if core]
    if len(coded) < HOLDINGS_MIN_COUNT:
        return []
    out: list[str] = []

    top_len, top_n = Counter(len(core) for _, _, core in coded).most_common(1)[0]
    if _mostly(top_n, len(coded)):
        odd = [f"line {_line_no(i)}: {raw} ({len(core)} chars)" for i, raw, core in coded
               if len(core) != top_len]
        out += _report(f"ticker length: {_pct(top_n, len(coded))} of tickers are "
                       f"{top_len} chars, these are not", odd)

    n_text = sum(_is_text(core) for _, _, core in coded)
    n_jp = sum(_is_jp_style(core) for _, _, core in coded)
    if _mostly(n_text, len(coded)):
        odd = [f"line {_line_no(i)}: {raw}" for i, raw, core in coded if not _is_text(core)]
        out += _report(f"ticker kind: {_pct(n_text, len(coded))} of tickers are text "
                       f"(at most one digit), these are not", odd)
    elif _mostly(n_jp, len(coded)):
        odd = [f"line {_line_no(i)}: {raw}" for i, raw, core in coded if not _is_jp_style(core)]
        out += _report(f"ticker kind: {_pct(n_jp, len(coded))} of tickers are digits with "
                       f"at most one letter, these are not", odd)
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


def check_exchanges(rows: list[Row]) -> list[str]:
    """Exchange should be a short form (TSE, NYSE, Nasdaq), not a full name."""
    odd = [f"line {_line_no(i)}: {r.exchange}" for i, r in enumerate(rows)
           if r.exchange and len(r.exchange) > EXCHANGE_SHORT_MAX_LEN]
    return _report(f"exchange not in short form (over {EXCHANGE_SHORT_MAX_LEN} chars)", odd)


def check_weights(rows: list[Row]) -> list[str]:
    """Weights are already percentages (20 means 20%). Flags the file if they
    add up to more than WEIGHT_SUM_MAX or to less than WEIGHT_SUM_MIN (holdings
    probably incomplete), and every row with no usable weight (reported
    separately so a gap isn't mistaken for a low total).
    The total leaves out rows whose Name is in WEIGHT_TOTAL_EXCLUDE_NAMES (currency hedges)."""
    parsed = [(i, parse_float(r.weight)) for i, r in enumerate(rows)]
    missing = [f"line {_line_no(i)}: {_label(rows[i])}" for i, w in parsed if w is None]
    weights = [(i, w) for i, w in parsed if w is not None]
    out: list[str] = _report("no usable weight", missing)
    if not weights:
        return out
    total = sum(w for i, w in weights if rows[i].name not in WEIGHT_TOTAL_EXCLUDE_NAMES)
    if total > WEIGHT_SUM_MAX:
        out.append(f"weight total: {format_weight(total)}% is over {WEIGHT_SUM_MAX:g}%")
    elif total < WEIGHT_SUM_MIN:
        suffix = f" (over {len(weights)} of {len(rows)} rows)" if missing else ""
        out.append(f"weight total: {format_weight(total)}% is below {WEIGHT_SUM_MIN:g}%{suffix}")
    return out


def _label(row: Row) -> str:
    return row.code or row.isin or row.name or "?"


# ---------------------------------------------------------------- helpers --
def _line_no(i: int) -> int:
    """Row index -> line number in the output CSV (header = line 1). Valid only
    because no step adds or drops rows between step 040 and step 090's write."""
    return i + 2


def _is_text(core: str) -> bool:
    """Mostly letters: at most one digit (AAPL, BRKB, JPWU6, 0HQN). A 1-letter + 1-digit
    ticker like 5A counts as number style instead, so the two styles never overlap."""
    letters = sum(ch.isalpha() for ch in core)
    digits = len(core) - letters
    return letters >= 1 and digits <= 1 and (letters >= 2 or digits == 0)


def _is_jp_style(core: str) -> bool:
    """At least one digit and at most one letter: 7021, 586A."""
    letters = sum(ch.isalpha() for ch in core)
    return letters <= 1 and any(ch.isdigit() for ch in core)


def _mostly(n: int, total: int) -> bool:
    """At least CHECK_DOMINANT_SHARE of the rows, but not all of them."""
    return total > 0 and n < total and n / total >= CHECK_DOMINANT_SHARE


def _pct(n: int, total: int) -> str:
    return f"{n / total:.0%} ({n}/{total})"


def _report(title: str, odd: list[str]) -> list[str]:
    if not odd:
        return []
    lines = [f"{title}: {len(odd)} row(s)"]
    lines += [f"    {line}" for line in odd[:CHECK_MAX_LISTED]]
    if len(odd) > CHECK_MAX_LISTED:
        lines.append(f"    ... and {len(odd) - CHECK_MAX_LISTED} more")
    return lines
