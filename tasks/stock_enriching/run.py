#!/usr/bin/env python3
"""
Enrich *_stocks.csv files with data looked up from per-region lookup files
in the "stocks" data directory, by Ticker.

Usage:
For each <REGION>_stocks.csv file in STOCKS_DIR (see constants.py), this
script:
  1. Loads the matching <REGION>_lookup.csv from STOCK_LOOKUP_DIR as a
     lookup table for that region.
  2. Matches each row's Ticker against that region's lookup table.
  3. If a ticker has multiple matches within the region's lookup file
     (e.g. same ticker listed against different exchanges/countries),
     prefers the row whose Country matches the region's mapped country
     (REGION_COUNTRY_MAP). Falls back to the first match otherwise, and
     logs it as an ambiguous match.
  4. Fills in / updates the ENRICH_COLUMNS (currently just ISIN) in the
     *_stocks.csv file, and overwrites the file in place. Lookup files
     are only ever read, never modified.
  5. Prints a summary: how many rows matched, unmatched tickers, and
     ambiguous matches that fell back to a non-country-preferred row.
  6. If a region has no <REGION>_lookup.csv, that region's *_stocks.csv
     is skipped (reported, not treated as fatal).

Adding a new column to enrich later (e.g. Exchange) is a one-line change:
just add it to ENRICH_COLUMNS in constants.py.

All paths, column lists, and region mappings live in constants.py.
"""

import csv
import glob
import os
from collections import defaultdict, Counter

from .constants import (
    STOCK_LOOKUP_DIR,
    STOCKS_DIR,
    ENRICH_COLUMNS,
    REGION_COUNTRY_MAP,
    STOCKS_FILE_PATTERN,
    LOOKUP_FILENAME_TEMPLATE,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def load_region_lookup(path):
    """Load a <REGION>_lookup.csv into a dict: ticker -> list of row dicts."""
    lookup = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("Ticker") or "").strip().upper()
            if not ticker:
                continue
            lookup[ticker].append(row)
    return lookup


def pick_best_match(candidates, preferred_country):
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

    if preferred_country:
        country_matches = [
            row for row in candidates
            if (row.get("Country") or "").strip().lower() == preferred_country.lower()
        ]
        if len(country_matches) == 1:
            return country_matches[0], False
        if len(country_matches) > 1:
            # Multiple rows for the same preferred country -- still
            # ambiguous, just pick the first one deterministically.
            return country_matches[0], True

    # No country match found (or no preferred country known) -> fall back
    # to the first candidate, but flag it as ambiguous.
    return candidates[0], True


def enrich_file(stocks_path, region_lookup, region):
    preferred_country = REGION_COUNTRY_MAP.get(region.upper())

    with open(stocks_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # Make sure all enrich columns exist in the output header, preserving
    # original column order and appending any new ones at the end.
    for col in ENRICH_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    matched = 0
    blank_ticker_rows = 0
    unmatched = Counter()
    ambiguous = Counter()

    for row in rows:
        ticker = (row.get("Ticker") or "").strip().upper()

        if not ticker:
            blank_ticker_rows += 1
            continue

        candidates = region_lookup.get(ticker)
        if not candidates:
            unmatched[ticker] += 1
            continue

        best, was_ambiguous = pick_best_match(candidates, preferred_country)
        if was_ambiguous:
            ambiguous[ticker] += 1

        for col in ENRICH_COLUMNS:
            value = (best.get(col) or "").strip()
            if value:
                row[col] = value

        matched += 1

    with open(stocks_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "total": len(rows),
        "matched": matched,
        "blank_ticker_rows": blank_ticker_rows,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


def format_counter(counter, limit=20):
    """Render a Counter of ticker -> occurrence count as 'TICKER (xN)' list,
    most frequent first, truncated to `limit` entries."""
    items = counter.most_common(limit)
    parts = [f"{ticker} (x{count})" if count > 1 else ticker for ticker, count in items]
    suffix = " ..." if len(counter) > limit else ""
    return ", ".join(parts) + suffix


def run():
    stocks_files = []
    skipped_filenames = []
    for path in glob.glob(os.path.join(STOCKS_DIR, "*_stocks.csv")):
        fname = os.path.basename(path)
        m = STOCKS_FILE_PATTERN.match(fname)
        if m:
            stocks_files.append((path, m.group(1)))
        else:
            skipped_filenames.append(fname)

    if not stocks_files:
        print(f"No *_stocks.csv files found in {STOCKS_DIR}.")
        return

    if skipped_filenames:
        print(f"Ignoring {len(skipped_filenames)} file(s) with unrecognized "
              f"naming pattern (expected <REGION>_stocks.csv): "
              f"{', '.join(sorted(skipped_filenames))}\n")

    skipped_regions = []

    for path, region in sorted(stocks_files):
        lookup_path = os.path.join(
            STOCK_LOOKUP_DIR, LOOKUP_FILENAME_TEMPLATE.format(region=region.upper())
        )

        if not os.path.isfile(lookup_path):
            print(f"Skipping {os.path.basename(path)} (region={region}): "
                  f"lookup file not found at {lookup_path}\n")
            skipped_regions.append(region)
            continue

        try:
            print(f"Loading {lookup_path} ...")
            region_lookup = load_region_lookup(lookup_path)
            print(f"  {sum(len(v) for v in region_lookup.values())} rows across "
                  f"{len(region_lookup)} unique tickers.")

            print(f"Enriching {os.path.basename(path)} (region={region}) ...")
            result = enrich_file(path, region_lookup, region)
        except Exception as exc:
            print(f"  ERROR processing region={region} ({os.path.basename(path)}): {exc}")
            print("  Skipping this region; other regions are unaffected.\n")
            skipped_regions.append(region)
            continue

        print(f"  {result['matched']}/{result['total']} rows matched.")
        if result["blank_ticker_rows"]:
            print(f"  {result['blank_ticker_rows']} row(s) had a blank Ticker (skipped).")
        if result["ambiguous"]:
            total_ambiguous = sum(result["ambiguous"].values())
            print(f"  {total_ambiguous} ambiguous match(es) across "
                  f"{len(result['ambiguous'])} ticker(s) (no unique country match, "
                  f"used first candidate): {format_counter(result['ambiguous'])}")
        if result["unmatched"]:
            total_unmatched = sum(result["unmatched"].values())
            print(f"  {total_unmatched} unmatched row(s) across "
                  f"{len(result['unmatched'])} ticker(s): "
                  f"{format_counter(result['unmatched'])}")
        print()

    if skipped_regions:
        print(f"Skipped {len(skipped_regions)} region(s) (no lookup file or error): "
              f"{', '.join(sorted(skipped_regions))}")
