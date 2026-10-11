"""Access to GLOBAL_lookup.csv (loaded once per run, cached), used by
step 050 (Exchange, rows without one), step 070 (Ticker refresh) and
step 080 (ISIN backfill)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


@dataclass
class LookupEntry:
    ticker: str
    name: str
    exchange: str
    isin: str
    asset_type: str


class AmbiguousLookupError(ValueError):
    """More than one lookup entry matches all supplied search fields."""


class GlobalLookup:
    def __init__(self, path: Path):
        # Indexes restrict candidates for the shared multi-column search.
        self._by_isin: dict[str, list[LookupEntry]] = {}
        self._by_ticker_loose: dict[str, list[LookupEntry]] = {}
        self._entries: list[LookupEntry] = []
        self._load(path)

    def _load(self, path: Path) -> None:
        seen = set()
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            names = {fn.strip().lower(): fn for fn in (reader.fieldnames or [])}
            for required in ("ticker", "isin"):
                if required not in names:
                    raise ValueError(f"lookup file {path} has no '{required}' column "
                                     f"(found: {', '.join(names) or 'none'})")

            def col(raw, key):
                real = names.get(key)
                return (raw.get(real) or "").strip() if real else ""

            for raw in reader:
                entry = LookupEntry(col(raw, "ticker"), col(raw, "name"),
                                    col(raw, "exchange"), col(raw, "isin"),
                                    col(raw, "asset_type"))
                identity = tuple(vars(entry).values())
                if identity in seen:
                    continue
                seen.add(identity)
                self._entries.append(entry)
                # Identical duplicate rows have already been removed.
                if entry.isin:
                    self._add(self._by_isin, entry.isin.upper(), entry)
                if entry.ticker:
                    key = entry.ticker.upper()
                    loose = _NON_ALNUM.sub("", key)
                    if loose:
                        self._add(self._by_ticker_loose, loose, entry)

    @staticmethod
    def _add(index: dict[str, list[LookupEntry]], key: str, entry: LookupEntry) -> None:
        bucket = index.setdefault(key, [])
        if entry not in bucket:
            bucket.append(entry)

    def find(self, **criteria: str | None) -> LookupEntry | None:
        """Match ALL supplied fields (one or more LookupEntry column names).
        Missing search values or no match return None. Multiple matches raise
        AmbiguousLookupError. Identical source records count as one entry.
        ISIN/ticker indexes restrict candidates before the remaining filters.
        """
        if not criteria:
            raise ValueError("at least one lookup field is required")
        unknown = criteria.keys() - LookupEntry.__dataclass_fields__.keys()
        if unknown:
            raise ValueError(f"unknown lookup field(s): {', '.join(sorted(unknown))}")
        wanted = {field: _normalise(field, value) for field, value in criteria.items()}
        if not all(wanted.values()):
            return None
        if "isin" in wanted:
            candidates = self._by_isin.get(wanted["isin"], [])
        elif "ticker" in wanted:
            candidates = self._by_ticker_loose.get(wanted["ticker"], [])
        else:
            candidates = self._entries
        matches = [entry for entry in candidates
                   if all((_tickers_match(criteria[field], entry)
                           if field == "ticker" else
                           _normalise(field, getattr(entry, field)) == value)
                          for field, value in wanted.items())]
        if len(matches) > 1:
            raise AmbiguousLookupError(f"{len(matches)} lookup entries match {criteria}")
        return matches[0] if matches else None


def _tickers_match(value: str | None, entry: LookupEntry) -> bool:
    if entry.exchange.strip().upper() == "SET":
        # Thai instrument suffixes are significant: AMATA.R is an NVDR,
        # whereas AMATAR is a separate REIT. Keep punctuation on SET;
        # only ignore the leading apostrophe used to protect CSV tickers.
        ticker = (value or "").strip().lstrip("'").upper()
        return ticker == entry.ticker.strip().lstrip("'").upper()
    return _normalise("ticker", value) == _normalise("ticker", entry.ticker)


def _normalise(field: str, value: str | None) -> str:
    value = (value or "").strip().upper()
    if field == "ticker":
        return _NON_ALNUM.sub("", value)
    return value


@lru_cache(maxsize=None)
def get_lookup(path: str) -> GlobalLookup:
    return GlobalLookup(Path(path))


@lru_cache(maxsize=None)
def get_exchange_names(path: str) -> dict[str, str]:
    """exchanges.csv -> {alias (stripped, case-folded): canonical lookup name}."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        names = {fn.strip().lower(): fn for fn in (reader.fieldnames or [])}
        for required in ("long_name", "short_name"):
            if required not in names:
                raise ValueError(f"exchanges file {path} has no '{required}' column "
                                 f"(found: {', '.join(names) or 'none'})")
        mapping = {}
        for raw in reader:
            long_name = (raw.get(names["long_name"]) or "").strip()
            short_name = (raw.get(names["short_name"]) or "").strip()
            if long_name and short_name:
                mapping[long_name.casefold()] = short_name
                mapping.setdefault(short_name.casefold(), short_name)
    return mapping
