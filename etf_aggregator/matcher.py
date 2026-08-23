from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd
from aggregating_constants import EXCHANGE_TO_REGION
from normalize import normalize_exchange, normalize_isin, normalize_region, normalize_ticker


def find_region(
    holding: pd.Series,
    stock_data: Dict[str, pd.DataFrame],
    region_hint: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return the (region_key, match_reason) for a holding, trying each
    criterion in priority order and stopping at the first one that matches
    a loaded region.

    Priority: region hint parsed from holdings filename -> explicit Region
    column on holding -> mapped Exchange -> ISIN country prefix.
    """
    if region_hint and region_hint in stock_data:
        return region_hint, "filename"

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


def find_stock(
    holding: pd.Series,
    stock_data: Dict[str, pd.DataFrame],
    region_hint: Optional[str] = None,
) -> Tuple[Optional[pd.Series], Optional[str], Optional[str]]:
    """Attempt to locate a stock in the database by ticker symbol.

    A holding that isn't a real equity (cash, FX, derivatives, ...) simply
    won't be found here and is reported as a miss like any other unmatched
    ticker -- there's no separate non-stock classification step.
    """
    ticker = normalize_ticker(holding.get("Code", ""))
    if not ticker:
        return None, None, None

    region, reason = find_region(holding, stock_data, region_hint)
    if region is None:
        return None, None, None

    df = stock_data[region]
    if ticker in df.index:
        return df.loc[ticker], region, reason

    return None, None, None
