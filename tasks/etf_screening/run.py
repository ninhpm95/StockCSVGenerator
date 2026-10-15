import os
import tempfile

import pandas as pd

from .helper import (
    normalize_ticker, load_lookup_fees, dedup_groups,
    force_preserve_bought, _require_columns,
)
from .constants import (
    DATA_CSV, CURRENT_CSV, LOOKUP_CSV, OUTPUT_CSV,
    MIN_AVG_VOLUME, VOLUME_CASCADE, TICKER_GROUPS, EXCLUDED_TICKERS,
    CSV_ENCODING, FEE_TEXT_TO_REMOVE, Fields as F,
)

_SOURCE_ROW = "_source_row"


def load_annotations(path):
    """Combine complementary annotations; reject conflicting duplicates."""
    current = pd.read_csv(path, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
    _require_columns(current, [F.TICKER, F.BOUGHT, F.NOTE], "CURRENT_CSV")
    current[F.TICKER] = current[F.TICKER].map(normalize_ticker)
    current = current[current[F.TICKER] != ""]
    rows = []
    for ticker, group in current.groupby(F.TICKER, sort=False):
        row = {F.TICKER: ticker}
        for column in (F.BOUGHT, F.NOTE):
            values = group.loc[group[column].str.strip() != "", column].unique()
            if len(values) > 1:
                raise ValueError(f"Conflicting {column} values for ticker {ticker}; output not written")
            row[column] = values[0] if len(values) else ""
        rows.append(row)
    return pd.DataFrame(rows, columns=[F.TICKER, F.BOUGHT, F.NOTE])


def select_rows(source, annotations, fee_lookup):
    """Screen a working copy and return explicit source row positions."""
    working = source[[F.TICKER, F.NAME, F.NOTIONAL_VOLUME]].copy()
    working[_SOURCE_ROW] = range(len(source))
    working[F.TICKER] = working[F.TICKER].map(normalize_ticker)
    working = working[working[F.TICKER] != ""].copy()
    raw_volume = working[F.NOTIONAL_VOLUME].str.replace(",", "", regex=False)
    working[F.NOTIONAL_VOLUME] = pd.to_numeric(raw_volume, errors="coerce")
    full = working.copy()

    start = len(working)
    working = working[working[F.NOTIONAL_VOLUME].fillna(0) >= MIN_AVG_VOLUME].copy()
    after_volume = len(working)
    working = working[working[F.NAME].str.strip() != ""].copy()
    after_name = len(working)
    excluded = {normalize_ticker(t) for t in EXCLUDED_TICKERS}
    working = working[~working[F.TICKER].isin(excluded)].copy()
    after_exclusion = len(working)
    groups = {
        name: [normalize_ticker(t) for t in tickers]
        for name, tickers in TICKER_GROUPS.items()
    }
    result = dedup_groups(working, groups, fee_lookup, VOLUME_CASCADE)
    result, missing_bought = force_preserve_bought(result, full, annotations)
    if missing_bought:
        raise ValueError(
            f"Bought tickers missing from DATA_CSV: {sorted(missing_bought)}; output not written"
        )
    result = result.drop_duplicates(subset=[F.TICKER], keep="first")
    positions = result[_SOURCE_ROW].sort_values().to_numpy(dtype=int)
    return positions, (start, after_volume, after_name, after_exclusion)


def build_output(source, positions, annotations):
    """Retain source cell text, restore annotations, and clean Fee footnotes."""
    result = source.iloc[positions].copy().reset_index(drop=True)
    keys = result[F.TICKER].map(normalize_ticker)
    lookup = annotations.set_index(F.TICKER)
    for column in (F.BOUGHT, F.NOTE):
        result[column] = keys.map(lookup[column]).fillna("")
    for text in FEE_TEXT_TO_REMOVE:
        result[F.FEE] = result[F.FEE].str.replace(text, "", regex=False)
    result.insert(result.columns.get_loc(F.FEE) + 1, F.BOUGHT, result.pop(F.BOUGHT))
    result[F.NOTE] = result.pop(F.NOTE)
    return result


def write_output(result, path):
    """Finish writing beside the destination before replacing its contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=CSV_ENCODING, newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            result.to_csv(stream, index=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def run():
    source = pd.read_csv(DATA_CSV, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
    _require_columns(source, [F.TICKER, F.NAME, F.NOTIONAL_VOLUME, F.FEE], "DATA_CSV")
    annotations = load_annotations(CURRENT_CSV)
    if LOOKUP_CSV.resolve() == DATA_CSV.resolve():
        print("Fee comparison uses DATA_CSV (LOOKUP_CSV points to the same file)")
    fee_lookup = load_lookup_fees(LOOKUP_CSV, encoding=CSV_ENCODING)
    positions, counts = select_rows(source, annotations, fee_lookup)
    result = build_output(source, positions, annotations)
    write_output(result, OUTPUT_CSV)

    start, after_volume, after_name, after_exclusion = counts
    print("=== ETF filter summary ===")
    print(f"Start:                     {start} rows")
    print(f"After volume filter:       {after_volume} rows (removed {start - after_volume})")
    print(f"After empty-Name drop:     {after_name} rows (removed {after_volume - after_name})")
    print(f"After excluding tickers:   {after_exclusion} rows (removed {after_name - after_exclusion})")
    print(f"Final output rows:         {len(result)} rows")
    print(f"Output written to {OUTPUT_CSV.resolve()}")
    return result
