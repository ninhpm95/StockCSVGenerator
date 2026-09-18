#!/usr/bin/env python3
"""
Enrich *_stocks.csv files with data looked up from per-region lookup files
in the "stocks" data directory, by Ticker.

This module is a subtask imported and run by main.py (e.g.
`from .run import run` then `run()`).

For each <REGION>_stocks.csv file in STOCKS_DIR (see constants.py), this
module:
  1. Loads the matching <REGION>_lookup.csv from STOCK_LOOKUP_DIR as a
     lookup table for that region.
  2. Matches each row's Ticker against that region's lookup table.
  3. If a ticker has multiple matches within the region's lookup file
     (e.g. same ticker listed against different exchanges/countries),
     prefers the row whose Country matches the region's mapped country
     (REGION_COUNTRY_MAP). Falls back to the first match otherwise, and
     counts it as an ambiguous match -- reported in the run summary,
     which covers both "no candidate matched the preferred country" and
     "more than one candidate matched the preferred country".
  4. Fills in / updates the ENRICH_COLUMNS (currently just ISIN) in the
     *_stocks.csv file. If anything actually changed (a value was written,
     or an enrich column had to be added to the header), overwrites the
     file in place -- atomically, via a temp file + os.replace, so a
     failure mid-write can't leave a truncated file behind. A file that
     matched nothing new is left untouched (reported as such). Lookup
     files are only ever read, never modified.
  5. Returns a structured summary (and prints progress/results as it
     goes): rows matched, rows actually enriched (a match can have no
     non-blank values to write), unmatched tickers, and ambiguous
     matches.
  6. If a region has no <REGION>_lookup.csv, or its stocks/lookup file is
     missing a Ticker column, that region is skipped (reported, not
     fatal to the rest of the run).

Adding a new column to enrich later (e.g. Exchange) is a one-line change:
add it to ENRICH_COLUMNS in constants.py. If a lookup file doesn't have
that column, it's reported as a warning rather than failing the run.

All paths, CSV column names, enrich columns, and region mappings live in
constants.py.
"""

import csv
import os
import tempfile
from collections import defaultdict, Counter

from .constants import (
    STOCK_LOOKUP_DIR,
    STOCKS_DIR,
    COL_TICKER,
    COL_COUNTRY,
    ENRICH_COLUMNS,
    REGION_COUNTRY_MAP,
    COUNTRY_ALIASES,
    STOCKS_FILE_PATTERN,
    LOOKUP_FILENAME_TEMPLATE,
    GLOBAL_LOOKUP_FILENAME,
)


def normalize_country(name):
    """Lowercase a Country value and resolve known aliases (e.g. "UK" ->
    "united kingdom") so lookup-file spelling variants still match
    REGION_COUNTRY_MAP. COUNTRY_ALIASES values are already lowercased at
    import time (see constants.py), so no second .lower() is needed here."""
    name = (name or "").strip().lower()
    return COUNTRY_ALIASES.get(name, name)


def build_header_map(fieldnames):
    """Map lowercase header name -> actual header name as it appears in the
    file, so column lookups can be case-insensitive (a file with "TICKER"
    or "ticker" instead of "Ticker" still works)."""
    return {(name or "").strip().lower(): name for name in (fieldnames or [])}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def load_region_lookup(path):
    """Load a <REGION>_lookup.csv into a dict: ticker -> list of row dicts.

    Returns (lookup, ticker_col, country_col, enrich_col_map, missing_enrich):
      - ticker_col / country_col are the actual header names found for
        COL_TICKER / COL_COUNTRY (case-insensitive match), or None if the
        file doesn't have that column.
      - enrich_col_map maps each ENRICH_COLUMNS entry -> the actual header
        name found for it in this file (case-insensitive), or None if the
        file doesn't have that column. Rows from `reader` are plain dicts
        keyed by the file's literal header spelling, so callers must look
        values up via this map rather than the ENRICH_COLUMNS name itself
        -- otherwise a lookup file spelled "isin" instead of "ISIN" would
        silently fail to match (row.get("ISIN") -> None) even though the
        column is present.
      - missing_enrich lists the same ENRICH_COLUMNS not found in this
        file's header, so the caller can warn.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        header_map = build_header_map(fieldnames)
        ticker_col = header_map.get(COL_TICKER.lower())
        country_col = header_map.get(COL_COUNTRY.lower())
        enrich_col_map = {col: header_map.get(col.lower()) for col in ENRICH_COLUMNS}
        missing_enrich = [col for col, actual in enrich_col_map.items() if actual is None]

        lookup = defaultdict(list)
        if ticker_col is not None:
            for row in reader:
                ticker = (row.get(ticker_col) or "").strip().upper()
                if not ticker:
                    continue
                lookup[ticker].append(row)

    return lookup, ticker_col, country_col, enrich_col_map, missing_enrich


class LazyGlobalLookup:
    """Wraps GLOBAL_lookup.csv so it's only read from disk if/when a region
    actually needs a fallback match -- and even then, at most once for the
    whole run (the parsed table is cached and reused across all regions).
    This matters because the file is large and most runs may never need it.

    If the file is missing, unreadable, or missing a Ticker column, that's
    reported once and treated as "no global fallback available" for the
    rest of the run -- it does not abort regions that don't need it, and
    the broken load is not retried on every subsequent region.
    """

    def __init__(self, path):
        self._path = path
        self._lookup = None  # populated lazily; stays None until first use
        self._country_col = None
        self._enrich_col_map = None

    def _load(self):
        if not os.path.isfile(self._path):
            print(f"Global fallback lookup file not found at "
                  f"{self._path}; unmatched tickers will not be "
                  f"retried against it.\n")
            return {}, None, {}

        print(f"Loading {self._path} (global fallback) ...")
        try:
            lookup, ticker_col, country_col, enrich_col_map, missing_enrich = load_region_lookup(self._path)
        except Exception as exc:
            print(f"  ERROR loading global fallback lookup ({exc}); "
                  f"continuing without global fallback for the rest of this run.\n")
            return {}, None, {}

        if ticker_col is None:
            print(f"  WARNING: no '{COL_TICKER}' column found in "
                  f"{os.path.basename(self._path)}; global fallback disabled.\n")
            return {}, None, {}

        if missing_enrich:
            print(f"  WARNING: {os.path.basename(self._path)} is missing "
                  f"column(s) {missing_enrich}; those won't be enriched via "
                  f"global fallback.")

        print(f"  {sum(len(v) for v in lookup.values())} rows "
              f"across {len(lookup)} unique tickers.\n")
        return lookup, country_col, enrich_col_map

    def get(self, ticker):
        if self._lookup is None:
            self._lookup, self._country_col, self._enrich_col_map = self._load()
        return self._lookup.get(ticker)

    @property
    def country_col(self):
        if self._lookup is None:
            self._lookup, self._country_col, self._enrich_col_map = self._load()
        return self._country_col

    @property
    def enrich_col_map(self):
        if self._lookup is None:
            self._lookup, self._country_col, self._enrich_col_map = self._load()
        return self._enrich_col_map


def pick_best_match(candidates, preferred_country, country_col):
    """
    Given multiple lookup rows for the same ticker (within one region's
    lookup file), pick the one whose Country matches preferred_country.
    Returns (row, was_ambiguous).

    "Ambiguous" covers two cases:
      - no candidate matches preferred_country (falls back to the first
        candidate), or
      - more than one candidate matches preferred_country (the choice
        among them is arbitrary, even though a match was found).
    """
    if len(candidates) == 1:
        return candidates[0], False

    if preferred_country and country_col is not None:
        preferred_norm = normalize_country(preferred_country)
        country_matches = [
            row for row in candidates
            if normalize_country(row.get(country_col)) == preferred_norm
        ]
        if len(country_matches) == 1:
            return country_matches[0], False
        if len(country_matches) > 1:
            # Multiple rows for the same preferred country -- still
            # ambiguous, just pick the first one deterministically.
            return country_matches[0], True

    # No country match found (or no preferred/Country column known) ->
    # fall back to the first candidate, but flag it as ambiguous.
    return candidates[0], True


def write_csv_atomic(path, fieldnames, rows):
    """Write rows to path via a temp file + os.replace, so a failure or
    interruption mid-write can't leave a truncated/partial file behind.

    extrasaction="ignore" guards against a malformed row (more fields than
    the header, which csv.DictReader stashes under a None key) reaching
    DictWriter and raising a confusing "fields not in fieldnames: None"
    error. In practice enrich_file (this function's only current caller)
    already checks for and rejects malformed rows before calling this, so
    that path shouldn't be reachable today -- this is a cheap backstop for
    any future caller that writes rows without that same upfront check.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".csv", dir=directory)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def enrich_file(stocks_path, region_lookup, country_col, region, enrich_col_map,
                 global_lookup=None):
    """
    enrich_col_map: {ENRICH_COLUMNS name -> actual header name in the
    *region* lookup file (or None if absent)}, as returned by
    load_region_lookup. Values looked up on a matched row must go through
    this map (not the ENRICH_COLUMNS name itself), since the lookup file
    may spell the column differently (e.g. "isin" vs "ISIN") -- see
    load_region_lookup's docstring.
    """
    preferred_country = REGION_COUNTRY_MAP.get(region.upper())

    with open(stocks_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # A row with more fields than the header lands its extras under a
    # None key courtesy of csv.DictReader. Fail this region clearly up
    # front rather than letting DictWriter choke on it later.
    malformed = sum(1 for row in rows if None in row)
    if malformed:
        raise ValueError(
            f"{malformed} row(s) in {os.path.basename(stocks_path)} have more "
            f"fields than the header row (malformed CSV); refusing to write"
        )

    header_map = build_header_map(fieldnames)
    stocks_ticker_col = header_map.get(COL_TICKER.lower())
    if stocks_ticker_col is None:
        raise ValueError(f"no '{COL_TICKER}' column found in {os.path.basename(stocks_path)}")

    # Make sure all enrich columns exist in the output header, preserving
    # original column order and appending any new ones at the end. Also
    # resolve each ENRICH_COLUMNS name to the actual header spelling
    # already present in *this* file (case-insensitive), so we don't
    # append a second "ISIN" column alongside an existing "isin" one.
    out_col = {}
    file_changed = False
    for col in ENRICH_COLUMNS:
        actual = header_map.get(col.lower())
        if actual is None:
            fieldnames.append(col)
            actual = col
            file_changed = True  # header itself changed even if no rows get a value
        out_col[col] = actual

    matched = 0
    enriched = 0  # subset of matched where a non-blank value was actually written
    blank_ticker_rows = 0
    unmatched = Counter()
    ambiguous = Counter()
    global_fallback = Counter()

    for row in rows:
        ticker = (row.get(stocks_ticker_col) or "").strip().upper()

        if not ticker:
            blank_ticker_rows += 1
            continue

        candidates = region_lookup.get(ticker)
        used_global = False
        active_country_col = country_col
        active_enrich_col_map = enrich_col_map
        if not candidates and global_lookup is not None:
            candidates = global_lookup.get(ticker)
            used_global = candidates is not None
            if used_global:
                active_country_col = global_lookup.country_col
                active_enrich_col_map = global_lookup.enrich_col_map

        if not candidates:
            unmatched[ticker] += 1
            continue

        best, was_ambiguous = pick_best_match(candidates, preferred_country, active_country_col)
        if was_ambiguous:
            ambiguous[ticker] += 1
        if used_global:
            global_fallback[ticker] += 1

        matched += 1
        row_enriched = False
        for col in ENRICH_COLUMNS:
            src = active_enrich_col_map.get(col)
            if src is None:
                continue  # this lookup source doesn't have the column at all
            value = (best.get(src) or "").strip()
            if value and row.get(out_col[col]) != value:
                row[out_col[col]] = value
                row_enriched = True
                file_changed = True
        if row_enriched:
            enriched += 1

    # Skip the rewrite entirely if nothing actually changed -- avoids mtime
    # churn and a needless line-ending/BOM normalization round-trip on
    # files where every row was already up to date or nothing matched.
    if file_changed:
        write_csv_atomic(stocks_path, fieldnames, rows)

    return {
        "total": len(rows),
        "matched": matched,
        "enriched": enriched,
        "blank_ticker_rows": blank_ticker_rows,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "global_fallback": global_fallback,
        "written": file_changed,
    }


def format_counter(counter, limit=20):
    """Render a Counter of ticker -> occurrence count as 'TICKER (xN)' list,
    most frequent first, truncated to `limit` entries."""
    items = counter.most_common(limit)
    parts = [f"{ticker} (x{count})" if count > 1 else ticker for ticker, count in items]
    suffix = f" ... and {len(counter) - limit} more" if len(counter) > limit else ""
    return ", ".join(parts) + suffix


def run():
    """Run the enrichment across all discovered regions.

    Prints progress and a summary to stdout as it goes, and returns a
    structured summary dict:
      {
        "stocks_dir": str,
        "skipped_filenames": [str, ...],
        "regions": [ {region, total, matched, enriched, blank_ticker_rows,
                       unmatched, ambiguous, global_fallback, written}, ... ],
        "skipped_regions": [ (region, reason), ... ],
        "error": str,  # only present if STOCKS_DIR itself doesn't exist;
                        # value is "stocks_dir_not_found" and the other
                        # keys above are left at their empty defaults
      }
    """
    summary = {
        "stocks_dir": STOCKS_DIR,
        "skipped_filenames": [],
        "regions": [],
        "skipped_regions": [],
    }

    if not os.path.isdir(STOCKS_DIR):
        print(f"Stocks directory not found: {STOCKS_DIR}")
        summary["error"] = "stocks_dir_not_found"
        return summary

    # os.scandir + regex, not glob: glob's own pattern matching is
    # case-sensitive on Linux, so glob.glob(".../*_stocks.csv") would never
    # return e.g. "JP_stocks.CSV" at all -- not even into skipped_filenames
    # -- even though STOCKS_FILE_PATTERN is explicitly case-insensitive and
    # documented (constants.py) to accept it. Matching filenames ourselves
    # against the case-insensitive regex means the pattern is the single
    # source of truth for what counts as a stocks file.
    stocks_files = []
    for entry in os.scandir(STOCKS_DIR):
        if not entry.is_file():
            continue
        m = STOCKS_FILE_PATTERN.match(entry.name)
        if m:
            stocks_files.append((entry.path, m.group(1)))
        elif entry.name.lower().endswith("_stocks.csv"):
            summary["skipped_filenames"].append(entry.name)

    # Report unrecognized filenames regardless of whether any recognized
    # *_stocks.csv files were found, so a directory of only-misnamed files
    # doesn't just print "no files found" with no explanation.
    if summary["skipped_filenames"]:
        print(f"Ignoring {len(summary['skipped_filenames'])} file(s) with unrecognized "
              f"naming pattern (expected <REGION>_stocks.csv): "
              f"{', '.join(sorted(summary['skipped_filenames']))}\n")

    if not stocks_files:
        print(f"No *_stocks.csv files found in {STOCKS_DIR}.")
        return summary

    # Shared across all regions and created up front, but the file itself is
    # only actually read from disk the first time some region's unmatched
    # ticker triggers a .get() call -- see LazyGlobalLookup. If every region
    # matches entirely within its own lookup file, GLOBAL_lookup.csv is
    # never opened at all.
    global_lookup_path = os.path.join(STOCK_LOOKUP_DIR, GLOBAL_LOOKUP_FILENAME)
    global_lookup = LazyGlobalLookup(global_lookup_path)

    # Sweep any orphaned temp files left behind by a hard-killed prior run
    # (write_csv_atomic cleans up its own on a normal exception, but a
    # SIGKILL mid-write can't run that cleanup). Harmless if any of this
    # fails -- it's just tidying, not correctness -- so don't let it abort
    # the run.
    try:
        for entry in os.scandir(STOCKS_DIR):
            if entry.is_file() and entry.name.startswith(".tmp_") and entry.name.endswith(".csv"):
                os.remove(entry.path)
    except OSError:
        pass

    for path, region in sorted(stocks_files):
        region = region.upper()  # normalize casing once; used consistently below
        lookup_path = os.path.join(
            STOCK_LOOKUP_DIR, LOOKUP_FILENAME_TEMPLATE.format(region=region)
        )

        if not os.path.isfile(lookup_path):
            print(f"Skipping {os.path.basename(path)} (region={region}): "
                  f"lookup file not found at {lookup_path}\n")
            summary["skipped_regions"].append((region, "no_lookup_file"))
            continue

        if region not in REGION_COUNTRY_MAP:
            print(f"  NOTE: no country mapping for region={region} in REGION_COUNTRY_MAP; "
                  f"any ticker with multiple candidates in its lookup file will be "
                  f"reported as ambiguous (no preferred country to disambiguate against).")

        try:
            print(f"Loading {lookup_path} ...")
            region_lookup, ticker_col, country_col, enrich_col_map, missing_enrich = load_region_lookup(lookup_path)

            if ticker_col is None:
                raise ValueError(f"no '{COL_TICKER}' column found in {os.path.basename(lookup_path)}")
            if missing_enrich:
                print(f"  WARNING: {os.path.basename(lookup_path)} is missing "
                      f"column(s) {missing_enrich}; those won't be enriched for this region.")

            print(f"  {sum(len(v) for v in region_lookup.values())} rows across "
                  f"{len(region_lookup)} unique tickers.")

            print(f"Enriching {os.path.basename(path)} (region={region}) ...")
            result = enrich_file(path, region_lookup, country_col, region, enrich_col_map, global_lookup)
        except Exception as exc:
            print(f"  ERROR processing region={region} ({os.path.basename(path)}): {exc}")
            print("  Skipping this region; other regions are unaffected.\n")
            summary["skipped_regions"].append((region, f"error: {exc}"))
            continue

        result["region"] = region
        summary["regions"].append(result)

        print(f"  {result['matched']}/{result['total']} rows matched "
              f"({result['enriched']} row(s) had a non-blank value written).")
        if not result["written"]:
            print(f"  No changes vs. the existing file -- left {os.path.basename(path)} untouched.")
        if result["global_fallback"]:
            total_global = sum(result["global_fallback"].values())
            print(f"  {total_global} row(s) matched only via GLOBAL fallback "
                  f"(not found in {region}_lookup.csv) across "
                  f"{len(result['global_fallback'])} ticker(s): "
                  f"{format_counter(result['global_fallback'])}")
        if result["blank_ticker_rows"]:
            print(f"  {result['blank_ticker_rows']} row(s) had a blank Ticker (skipped).")
        if result["ambiguous"]:
            total_ambiguous = sum(result["ambiguous"].values())
            print(f"  {total_ambiguous} ambiguous match(es) across "
                  f"{len(result['ambiguous'])} ticker(s) (no unique preferred-country "
                  f"match -- either none or multiple candidates matched it; first "
                  f"candidate used): {format_counter(result['ambiguous'])}")
        if result["unmatched"]:
            total_unmatched = sum(result["unmatched"].values())
            print(f"  {total_unmatched} unmatched row(s) across "
                  f"{len(result['unmatched'])} ticker(s): "
                  f"{format_counter(result['unmatched'])}")
        print()

    if summary["skipped_regions"]:
        no_lookup = sorted(r for r, reason in summary["skipped_regions"] if reason == "no_lookup_file")
        errored = sorted(r for r, reason in summary["skipped_regions"] if reason != "no_lookup_file")
        parts = []
        if no_lookup:
            parts.append(f"no lookup file: {', '.join(no_lookup)}")
        if errored:
            parts.append(f"error: {', '.join(errored)}")
        print(f"Skipped {len(summary['skipped_regions'])} region(s) -- " + "; ".join(parts))

    return summary
