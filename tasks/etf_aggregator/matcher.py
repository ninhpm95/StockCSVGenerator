from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from .constants import EXCHANGE_TO_REGION
from .normalize import normalize_exchange, normalize_isin, normalize_region, normalize_ticker


def find_region(
    holding: pd.Series,
    stock_data: Dict[str, pd.DataFrame],
) -> Tuple[Optional[str], Optional[str]]:
    """Return the (region_key, match_reason) for a holding, trying each
    criterion in priority order and stopping at the first one that matches
    a loaded region.

    Priority: explicit Region column on holding -> mapped Exchange -> ISIN
    country prefix.
    """
    region = normalize_region(holding.get("Region", ""))
    if region in stock_data:
        return region, "region"

    exchange = normalize_exchange(holding.get("Exchange", ""))
    mapped_region = EXCHANGE_TO_REGION.get(exchange)
    if mapped_region in stock_data:
        return mapped_region, f"exchange:{exchange}"

    isin = normalize_isin(holding.get("ISIN", ""))
    isin_region = isin[:2]
    if len(isin_region) == 2 and isin_region in stock_data:
        return isin_region, "isin"

    return None, None


def find_stock_by_isin(
    isin: str,
    stock_data: Dict[str, pd.DataFrame],
) -> Tuple[Optional[pd.Series], Optional[str]]:
    """Search every loaded stock file for a row whose ISIN matches, ignoring
    region entirely. Used as a fallback when a holding's region can't be
    determined some other way.

    Returns (None, None) if `isin` is blank, or if no stock file has an
    ISIN column to search -- both are "can't do this lookup" cases, not
    "searched and found nothing".
    """
    if not isin:
        return None, None

    for region, df in stock_data.items():
        if "ISIN" not in df.columns:
            continue

        matches = df.index[df["ISIN"].map(normalize_isin) == isin]
        if len(matches):
            return df.loc[matches[0]], region

    return None, None


def find_stock(
    holding: pd.Series,
    stock_data: Dict[str, pd.DataFrame],
) -> Tuple[Optional[pd.Series], Optional[str], Optional[str]]:
    """Attempt to locate a stock in the database, primarily by ticker symbol
    within the holding's region.

    A holding that isn't a real equity (cash, FX, derivatives, ...) simply
    won't be found here and is reported as a miss like any other unmatched
    ticker -- there's no separate non-stock classification step.
    """
    ticker = normalize_ticker(holding.get("Code", ""))
    if not ticker:
        return None, None, None

    region, reason = find_region(holding, stock_data)

    if region is not None:
        df = stock_data[region]
        if ticker in df.index:
            return df.loc[ticker], region, reason

    # Either the region couldn't be determined, or it could but the
    # ticker wasn't found in that region's file (e.g. a stale/mismatched
    # ticker). Either way, fall back to matching by ISIN across all
    # loaded stock files instead of giving up outright. This only works
    # if the holding actually carries an ISIN and at least one stock file
    # has an ISIN column; find_stock_by_isin returns (None, None) otherwise.
    isin = normalize_isin(holding.get("ISIN", ""))
    stock, isin_region = find_stock_by_isin(isin, stock_data)
    if stock is not None:
        return stock, isin_region, "isin_search"

    return None, None, None
