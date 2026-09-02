"""
Kept separate so paths/columns/region mappings can be tweaked without
touching the enrichment logic.
"""

import os
import re

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------

# Per-region lookup files live in the parent-of-parent folder's "data/stocks"
# dir:
#   <this_dir>/../../data/stocks/<REGION>_lookup.csv
# These are treated as read-only reference data and are never written to.
STOCK_LOOKUP_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "stocks"
))

# *_stocks.csv files live in this folder's "data" dir:
#   <this_dir>/data/*_stocks.csv
# These are copies pulled in from elsewhere, so they're safe to overwrite
# in place -- no backup/dry-run needed.
STOCKS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

# ----------------------------------------------------------------------------
# Enrichment columns
# ----------------------------------------------------------------------------

# Columns to pull from the region lookup files into the *_stocks.csv files.
# Add more here later (e.g. "Exchange") -- no other code changes needed
# as long as the column exists in the lookup files. A blank value in the
# lookup file will NOT clear an existing value in the stocks file -- only
# non-blank lookup values overwrite.
ENRICH_COLUMNS = ["ISIN"]

# ----------------------------------------------------------------------------
# Region mapping
# ----------------------------------------------------------------------------

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

# ----------------------------------------------------------------------------
# Filename patterns
# ----------------------------------------------------------------------------

# Region prefix can be 2-4 uppercase letters (e.g. "JP", "APAC").
STOCKS_FILE_PATTERN = re.compile(r"^([A-Za-z]{2,4})_stocks\.csv$")
LOOKUP_FILENAME_TEMPLATE = "{region}_lookup.csv"
