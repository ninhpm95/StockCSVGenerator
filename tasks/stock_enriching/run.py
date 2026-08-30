#!/usr/bin/env python3
"""
Enrich *_stocks.csv files with data looked up from per-region lookup files
in the "stocks" data directory, by Ticker.

Usage:
    python enrich_stocks.py --data-dir /data

For each <REGION>_stocks.csv file in --data-dir, this script:
  1. Loads the matching <REGION>_lookup.csv from STOCK_LOOKUP_DIR as a
     lookup table for that region.
  2. Matches each row's Ticker against that region's lookup table.
  3. If a ticker has multiple matches within the region's lookup file
     (e.g. same ticker listed against different exchanges/countries),
     prefers the row whose Country matches the region's mapped country
     (REGION_COUNTRY_MAP). Falls back to the first match otherwise, and
     logs it as an ambiguous match.
  4. Fills in / updates the ENRICH_COLUMNS (currently just ISIN) in the
     *_stocks.csv file, and overwrites the file in place.
  5. Prints a summary: how many rows matched, unmatched tickers, and
     ambiguous matches that fell back to a non-country-preferred row.
  6. If a region has no <REGION>_lookup.csv, that region's *_stocks.csv
     is skipped (reported, not treated as fatal).

Adding a new column to enrich later (e.g. Exchange) is a one-line change:
just add it to ENRICH_COLUMNS below.
"""

import csv
import glob
import os
import re
import sys
from collections import defaultdict

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

# Per-region lookup files now live in the parent-of-parent folder's "data/stocks"
# dir:
#   <script_dir>/../../data/stocks/<REGION>_lookup.csv
STOCK_LOOKUP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "stocks"
)

# *_stocks.csv files live in the current folder's "data" dir:
#   <script_dir>/data/*_stocks.csv
STOCKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Columns to pull from the region lookup files into the *_stocks.csv files.
# Add more here later (e.g. "Exchange") -- no other code changes needed
# as long as the column exists in the lookup files.
ENRICH_COLUMNS = ["ISIN"]

# Maps the region prefix used in "<REGION>_stocks.csv" / "<REGION>_lookup.csv"
# filenames to the "Country" value used inside the lookup files (used only to
# disambiguate a ticker that appears more than once within the same region's
# lookup file). Extend as needed.
REGION_COUNTRY_MAP = {
    "JP": "Japan",
    "US": "United States",
    "HK": "Hong Kong",
    "IN": "India",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "CA": "Canada",
    "AU": "Australia",
    "SG": "Singapore",
    "CN": "China",
    "KR": "South Korea",
    "TW": "Taiwan",
}

STOCKS_FILE_PATTERN = re.compile(r"^([A-Za-z]{2,3})_stocks\.csv$")
LOOKUP_FILENAME_TEMPLATE = "{region}_lookup.csv"


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
    """
    if len(candidates) == 1:
        return candidates[0], False

    if preferred_country:
        for row in candidates:
            if (row.get("Country") or "").strip().lower() == preferred_country.lower():
                return row, False

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
    unmatched = []
    ambiguous = []

    for row in rows:
        ticker = (row.get("Ticker") or "").strip().upper()
        candidates = region_lookup.get(ticker)

        if not candidates:
            unmatched.append(ticker)
            continue

        best, was_ambiguous = pick_best_match(candidates, preferred_country)
        if was_ambiguous:
            ambiguous.append(ticker)

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
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


def run():
    stocks_files = []
    for path in glob.glob(os.path.join(STOCKS_DIR, "*_stocks.csv")):
        fname = os.path.basename(path)
        m = STOCKS_FILE_PATTERN.match(fname)
        if m:
            stocks_files.append((path, m.group(1)))

    if not stocks_files:
        print(f"No *_stocks.csv files found in {STOCKS_DIR}.")
        return

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

        print(f"Loading {lookup_path} ...")
        region_lookup = load_region_lookup(lookup_path)
        print(f"  {sum(len(v) for v in region_lookup.values())} rows across "
              f"{len(region_lookup)} unique tickers.")

        print(f"Enriching {os.path.basename(path)} (region={region}) ...")
        result = enrich_file(path, region_lookup, region)
        print(f"  {result['matched']}/{result['total']} rows matched.")
        if result["ambiguous"]:
            print(f"  {len(result['ambiguous'])} ambiguous (no country match, used first): "
                  f"{', '.join(result['ambiguous'][:20])}"
                  f"{' ...' if len(result['ambiguous']) > 20 else ''}")
        if result["unmatched"]:
            print(f"  {len(result['unmatched'])} unmatched tickers: "
                  f"{', '.join(result['unmatched'][:20])}"
                  f"{' ...' if len(result['unmatched']) > 20 else ''}")
        print()

    if skipped_regions:
        print(f"Skipped {len(skipped_regions)} region(s) with no lookup file: "
              f"{', '.join(sorted(skipped_regions))}")
