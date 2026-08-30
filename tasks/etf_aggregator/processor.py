from __future__ import annotations

from typing import Dict, List, Tuple, TypedDict
import logging
import pandas as pd

from .constants import AGGREGATE_COLUMNS, AGGREGATION_METHODS, OVERWRITE_EXISTING, MIN_WEIGHT_THRESHOLD
from .normalize import normalize_isin, normalize_ticker
from .aggregator import rating_label, weighted_average, weighted_harmonic_mean
from .loaders import find_holdings_file, extract_region_from_filename
from .matcher import find_stock
from .parsers import parse_holdings
from .stats import ETFStats

logger = logging.getLogger(__name__)


class MatchedHolding(TypedDict):
    holding_code: str
    holding_isin: str
    region: str
    weight: float
    normalized_weight: float
    stock: pd.Series


def process_etf(
    etf_row: pd.Series,
    stock_data: Dict[str, pd.DataFrame],
) -> Tuple[pd.Series, ETFStats]:
    """Process a single ETF row against the loaded stock database."""
    result = etf_row.astype(object)
    ticker = normalize_ticker(etf_row.get("Ticker", ""))

    if not ticker:
        logger.warning("ETF row has no Ticker value; leaving row unchanged.")
        return result, ETFStats()

    stats = ETFStats()
    holdings_path = find_holdings_file(ticker)
    if holdings_path is None:
        logger.warning("ETF %s: missing holdings file; leaving row unchanged.", ticker)
        return result, stats

    logger.info("ETF %s (%s): using %s", ticker, etf_row.get("Name", ""), holdings_path.name)
    region_hint = extract_region_from_filename(holdings_path)

    try:
        holdings = parse_holdings(holdings_path)
    except Exception:
        logger.exception("ETF %s: parsing error in %s", ticker, holdings_path.name)
        return result, stats

    stats.holdings = len(holdings)
    matched_rows = _match_holdings(ticker, holdings, stock_data, region_hint, stats)

    if not matched_rows:
        logger.warning("ETF %s: zero matched stock holdings.", ticker)
        return result, stats

    total_weight = sum(item["weight"] for item in matched_rows)
    stats.matched_weight = total_weight

    # --- COVERAGE GUARD: Skip aggregation if matched weight is below 80% ---
    if total_weight < MIN_WEIGHT_THRESHOLD:
        logger.warning(
            "ETF %s: matched weight (%.1f%%) is below the required %.0f%% threshold; leaving row unchanged.",
            ticker,
            total_weight * 100,
            MIN_WEIGHT_THRESHOLD * 100,
        )
        return result, stats

    for item in matched_rows:
        item["normalized_weight"] = item["weight"] / total_weight

    _apply_aggregates(result, matched_rows)
    _apply_growth(result)
    return result, stats


def _match_holdings(
    ticker: str,
    holdings: pd.DataFrame,
    stock_data: Dict[str, pd.DataFrame],
    region_hint: str | None,
    stats: ETFStats,
) -> List[MatchedHolding]:
    """Look up each holding in the stock database, returning the matches.
    A holding that isn't a real equity (cash, FX, derivatives, ...) is never found here and is simply counted as a miss.
    """
    matched_rows: List[MatchedHolding] = []

    for _, holding in holdings.iterrows():
        code = normalize_ticker(holding.get("Code", ""))
        isin = normalize_isin(holding.get("ISIN", ""))

        stock, region, _reason = find_stock(holding, stock_data, region_hint)
        if stock is None:
            stats.missed += 1
            logger.warning("ETF %s | %s | %s | NOT FOUND", ticker, code, isin)
            continue

        stats.matched += 1
        pct = holding.get("pct", float("nan"))

        if pd.isna(pct) or pct <= 0:
            logger.warning("ETF %s | %s | %s | matched %s but invalid weight=%s", ticker, code, isin, region, pct)
            continue

        matched_rows.append(
            MatchedHolding(
                holding_code=code,
                holding_isin=isin,
                region=region,
                weight=float(pct),
                normalized_weight=0.0,
                stock=stock,
            )
        )

    return matched_rows


def _apply_aggregates(result: pd.Series, matched_rows: List[MatchedHolding]) -> None:
    """Fill each AGGREGATE_COLUMNS metric onto `result`, using the aggregation
    method configured for that column (see AGGREGATION_METHODS)."""
    for column in AGGREGATE_COLUMNS:
        if column not in result.index or (not OVERWRITE_EXISTING and pd.notna(result[column])):
            continue

        method = AGGREGATION_METHODS.get(column, "weighted_average")
        if method == "skip":
            logger.info("Column %s has no defined aggregation formula yet; skipping.", column)
            continue

        vals, weights = [], []
        for item in matched_rows:
            stock = item["stock"]
            if column in stock.index:
                val = pd.to_numeric(stock[column], errors="coerce")
                if pd.notna(val):
                    vals.append(val)
                    weights.append(item["normalized_weight"])

        if not vals:
            result[column] = float("nan")
            continue

        if method == "harmonic":
            result[column] = weighted_harmonic_mean(pd.Series(vals), pd.Series(weights))
        else:
            result[column] = weighted_average(pd.Series(vals), pd.Series(weights))

    if "Avg Rating Score" in result.index and "Avg Rating Label" in result.index:
        if OVERWRITE_EXISTING or pd.isna(result["Avg Rating Label"]):
            result["Avg Rating Label"] = rating_label(pd.to_numeric(result["Avg Rating Score"], errors="coerce"))


def _apply_growth(result: pd.Series) -> None:
    """Derive Growth = trailing PE / forward PE, once both have been
    aggregated above. Left as NaN if either value is missing or forward PE
    is zero."""
    if "Growth" not in result.index or (not OVERWRITE_EXISTING and pd.notna(result["Growth"])):
        return

    trailing_pe = result.get("PE ratio")
    forward_pe = result.get("Forward PE ratio")

    has_numbers = (
        isinstance(trailing_pe, (int, float))
        and isinstance(forward_pe, (int, float))
        and pd.notna(trailing_pe)
        and pd.notna(forward_pe)
        and forward_pe != 0
    )
    result["Growth"] = (trailing_pe / forward_pe) if has_numbers else float("nan")
