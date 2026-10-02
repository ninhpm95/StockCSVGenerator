"""Access to GLOBAL_lookup.csv (loaded once per run, cached), used by
step 060 (ISIN backfill), step 070 (Ticker refresh, ticker-named files only) and
step 080 (Exchange, rows without one)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

_EXCHANGE_ALIASES = {
    "NYSE": "NYSE", "XNYS": "NYSE", "NEW YORK STOCK EXCHANGE": "NYSE",
    "NEW YORK STOCK EXCHANGE INC.": "NYSE", "NYSE ARCA": "NYSE ARCA", "ARCA": "NYSE ARCA",
    "NASDAQ": "NASDAQ", "XNAS": "NASDAQ", "NASDAQ STOCK MARKET": "NASDAQ",
}


@dataclass
class LookupEntry:
    ticker: str
    name: str
    exchange: str
    isin: str
    asset_type: str


class GlobalLookup:
    def __init__(self, path: Path):
        # Stored as _by_isin / _by_ticker / _by_ticker_loose (not
        # by_isin / by_ticker) so the dicts don't shadow the by_isin() /
        # by_ticker() lookup methods below - an instance attribute would
        # otherwise win over a same-named method.
        self._by_isin: dict[str, list[LookupEntry]] = {}
        self._by_ticker: dict[str, list[LookupEntry]] = {}
        self._by_ticker_loose: dict[str, list[LookupEntry]] = {}
        self._load(path)

    def _load(self, path: Path) -> None:
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
                # identical duplicate rows don't count, in any of the three indexes
                if entry.isin:
                    self._add(self._by_isin, entry.isin.upper(), entry)
                if entry.ticker:
                    key = entry.ticker.upper()
                    self._add(self._by_ticker, key, entry)
                    loose = _NON_ALNUM.sub("", key)
                    if loose:
                        self._add(self._by_ticker_loose, loose, entry)

    @staticmethod
    def _add(index: dict[str, list[LookupEntry]], key: str, entry: LookupEntry) -> None:
        bucket = index.setdefault(key, [])
        if entry not in bucket:
            bucket.append(entry)

    def by_isin(self, isin: str | None) -> list[LookupEntry]:
        """All lookup entries with this ISIN (empty list if none)."""
        return list(self._by_isin.get((isin or "").strip().upper(), []))

    @staticmethod
    def narrow_by_ticker(entries: list[LookupEntry], ticker: str | None) -> list[LookupEntry]:
        """Entries whose ticker matches (separators ignored: 'BRK B' ~ 'BRK.B'); [] if none/no ticker."""
        want = _NON_ALNUM.sub("", (ticker or "").upper())
        if not want:
            return []
        return [e for e in entries if _NON_ALNUM.sub("", e.ticker.upper()) == want]

    @staticmethod
    def narrow_by_exchange(entries: list[LookupEntry], exchange: str | None) -> list[LookupEntry]:
        """Entries whose exchange matches (aliases normalised); [] if no exchange given or none match."""
        if not exchange:
            return []
        want = _norm_exchange(exchange)
        return [e for e in entries if _norm_exchange(e.exchange) == want]

    def by_ticker(self, ticker: str | None, exchange: str | None = None) -> LookupEntry | None:
        """Exact ticker match ('BRK B' also finds 'BRK.B'). Ambiguous tickers are
        only resolved when the exchange matches; otherwise None (refuse to guess)."""
        key = (ticker or "").strip().upper()
        if not key:
            return None
        hits = self._by_ticker.get(key) or self._by_ticker_loose.get(_NON_ALNUM.sub("", key)) or []
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        if exchange:
            want = _norm_exchange(exchange)
            for hit in hits:
                if _norm_exchange(hit.exchange) == want:
                    return hit
        return None


def _norm_exchange(exchange: str) -> str:
    e = exchange.strip().upper()
    return _EXCHANGE_ALIASES.get(e, e)


@lru_cache(maxsize=None)
def get_lookup(path: str) -> GlobalLookup:
    return GlobalLookup(Path(path))
