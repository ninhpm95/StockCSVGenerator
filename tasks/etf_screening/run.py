import logging
import pandas as pd

from .filter_helper import (
    normalize_ticker,
    load_lookup_fees,
    dedup_groups,
    force_preserve_bought,
    _require_columns,
)
from .constants import (
    DATA_CSV,
    CURRENT_CSV,
    LOOKUP_CSV,
    OUTPUT_CSV,
    OUTPUT_DIR,
    MIN_AVG_VOLUME,
    VOLUME_CASCADE,
    TICKER_GROUPS,
    EXCLUDED_TICKERS,
    CSV_ENCODING,
    OVERWRITE_FEE_FROM_LOOKUP,
    Fields as F,
)

logger = logging.getLogger(__name__)


def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(DATA_CSV, dtype=str, encoding=CSV_ENCODING)
    _require_columns(df, [F.TICKER, F.NAME, F.NOTIONAL_VOLUME], "DATA_CSV")
    df[F.TICKER] = df[F.TICKER].map(normalize_ticker)
    # Rows with no usable ticker can't be meaningfully filtered, grouped, or
    # merged later -- drop them now rather than letting them collide on a
    # shared "" key downstream.
    df = df[df[F.TICKER] != ""].copy()

    # Preserve user notes
    current_df = pd.read_csv(CURRENT_CSV, dtype=str, encoding=CSV_ENCODING)
    _require_columns(current_df, [F.TICKER, F.BOUGHT, F.NOTE], "CURRENT_CSV")
    current_df[F.TICKER] = current_df[F.TICKER].map(normalize_ticker)
    current_df = current_df[current_df[F.TICKER] != ""].copy()

    # If a ticker appears more than once in CURRENT_CSV, keep whichever row
    # actually carries data. Sorting so a non-empty Bought/Note comes first
    # means a real annotation is never thrown away in favor of a blank
    # duplicate row that merely happened to appear earlier in the file.
    # kind="stable" (+ the F.TICKER tiebreak isn't needed here since we sort
    # on a single boolean key) keeps "which duplicate wins" deterministic
    # and tied to file order, rather than to quicksort's default, which is
    # not guaranteed stable for equal keys.
    has_data = (
        current_df[F.BOUGHT].notna() & (current_df[F.BOUGHT].astype(str).str.strip() != "")
    ) | (
        current_df[F.NOTE].notna() & (current_df[F.NOTE].astype(str).str.strip() != "")
    )
    current_df = current_df.assign(_has_data=has_data)
    current_df = current_df.sort_values("_has_data", ascending=False, kind="stable")
    current_df = current_df.drop_duplicates(subset=[F.TICKER], keep="first")
    current_df = current_df.drop(columns=["_has_data"])

    # Keep only columns we want to restore later
    current_df = current_df[[F.TICKER, F.BOUGHT, F.NOTE]].copy()

    # Remove them from main dataframe if they exist
    for col in [F.BOUGHT, F.NOTE]:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Numeric coercion for the columns we filter/sort on. Strip thousands
    # separators ("30,000,000") first -- left as-is, to_numeric would coerce
    # those to NaN and the row would silently vanish in Step 1 below. Use
    # Int64 (nullable) rather than float64 so whole-number volumes don't
    # come out as "35000000.0" in the final CSV.
    raw_volume = df[F.NOTIONAL_VOLUME].astype(str).str.replace(",", "", regex=False)
    df[F.NOTIONAL_VOLUME] = pd.to_numeric(raw_volume, errors="coerce").astype("Int64")

    # Snapshot before any filtering, so rows with a non-empty "Bought" in
    # CURRENT_CSV can be recovered later even if a filter step below would
    # otherwise have dropped them.
    full_df = df.copy()

    start_n = len(df)

    # ---- Step 1: minimum avg volume (first, to shrink the dataset early) ----
    df = df[df[F.NOTIONAL_VOLUME].fillna(0) >= MIN_AVG_VOLUME].copy()
    after_volume_n = len(df)

    # ---- Step 2: drop rows with empty/missing Name ----
    df = df[df[F.NAME].notna() & (df[F.NAME].str.strip() != "")].copy()
    after_name_n = len(df)

    # ---- Step 3: exclude explicit tickers ----
    excluded_set = {normalize_ticker(t) for t in EXCLUDED_TICKERS}
    df = df[~df[F.TICKER].isin(excluded_set)].copy()
    after_exclude_n = len(df)

    # ---- Step 4: Fee lookup CSV ----
    # See the LOOKUP_CSV comment in constants.py for what "found in lookup
    # CSV" actually means today. Warn at runtime too, so it can't be missed
    # just because nobody happened to read that comment.
    if LOOKUP_CSV.resolve() == DATA_CSV.resolve():
        logger.warning(
            "LOOKUP_CSV points at the same file as DATA_CSV; Step 5's "
            "'found in lookup CSV' only means 'has a parseable Fee in the "
            "source data', not membership in a separately curated fee list."
        )
    fee_lookup = load_lookup_fees(LOOKUP_CSV, encoding=CSV_ENCODING)

    if OVERWRITE_FEE_FROM_LOOKUP:
        fee_overrides = df[F.TICKER].map(fee_lookup)
        df[F.FEE] = fee_overrides.where(fee_overrides.notna(), df.get(F.FEE))

    # ---- Step 5: group dedup by cascading volume + lowest fee ----
    normalized_groups = {
        group_name: [normalize_ticker(t) for t in tickers]
        for group_name, tickers in TICKER_GROUPS.items()
    }
    result = dedup_groups(df, normalized_groups, fee_lookup, VOLUME_CASCADE, logger=logger)

    # ---- Force-preserve rows with a non-empty "Bought" ----
    # These must always survive, regardless of any filter above (volume, name,
    # explicit exclusion, or losing a group's dedup). This intentionally takes
    # priority over EXCLUDED_TICKERS too: an owned position stays visible even
    # if it's on the exclusion list. Pull the full row back in from full_df
    # (the pre-filter snapshot) if it's missing from result.
    result, _still_missing_bought = force_preserve_bought(result, full_df, current_df, logger=logger)

    # A ticker listed in more than one TICKER_GROUPS entry can independently
    # win its dedup round in each group, so guard against emitting it twice.
    result = result.drop_duplicates(subset=[F.TICKER], keep="first")

    # Keep the original DATA_CSV row order rather than sorting alphabetically.
    # Every row in `result` -- whether it came from dedup_groups() or was
    # recovered by force_preserve_bought() -- still carries its original
    # index from the initial read_csv (filtering/selection preserves index
    # values), so sorting on that index restores the source file's order.
    # NOTE: this guarantee depends on the index never being reset before
    # this point, in this file OR inside dedup_groups()/force_preserve_bought()
    # in filter_helper.py -- if any of those add a .reset_index(), this
    # breaks silently.
    result = result.sort_index().reset_index(drop=True)

    result = result.merge(current_df, on=F.TICKER, how="left")

    # Move Bought after Fee (only if both columns are actually present)
    if F.BOUGHT in result.columns and F.FEE in result.columns:
        result.insert(
            result.columns.get_loc(F.FEE) + 1,
            F.BOUGHT,
            result.pop(F.BOUGHT),
        )

    # Move Note to end
    if F.NOTE in result.columns:
        result[F.NOTE] = result.pop(F.NOTE)

    result.to_csv(OUTPUT_CSV, index=False, encoding=CSV_ENCODING)

    logger.info("=== ETF filter summary ===")
    logger.info("Start:                     %d rows", start_n)
    logger.info(
        "After volume filter:       %d rows (removed %d)",
        after_volume_n, start_n - after_volume_n,
    )
    logger.info(
        "After empty-Name drop:     %d rows (removed %d)",
        after_name_n, after_volume_n - after_name_n,
    )
    logger.info(
        "After excluding tickers:   %d rows (removed %d)",
        after_exclude_n, after_name_n - after_exclude_n,
    )
    logger.info("Final output rows:         %d rows", len(result))
    logger.info("Output written to %s", OUTPUT_CSV.resolve())

    return result


if __name__ == "__main__":
    # Ensures logger.info/.warning calls above actually go somewhere when
    # this module is run directly, rather than silently doing nothing
    # because no handler is configured. If this module is imported as part
    # of a larger app, that app's own logging.basicConfig (or dictConfig)
    # takes precedence instead.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run()
