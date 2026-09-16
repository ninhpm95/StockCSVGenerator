import logging
import pandas as pd

from .filter_helper import normalize_ticker, load_lookup_fees
from .constants import (
    DATA_CSV,
    CURRENT_CSV,
    LOOKUP_CSV,
    OUTPUT_CSV,
    MIN_AVG_VOLUME,
    VOLUME_CASCADE,
    TICKER_GROUPS,
    EXCLUDED_TICKERS,
    Fields as F,
)

logger = logging.getLogger(__name__)


def _require_columns(df, columns, label):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{label} is missing required column(s): {missing}")


def run():
    df = pd.read_csv(DATA_CSV, dtype=str)
    _require_columns(df, [F.TICKER, F.NAME, F.AVG_VOLUME], "DATA_CSV")
    df[F.TICKER] = df[F.TICKER].map(normalize_ticker)
    # Rows with no usable ticker can't be meaningfully filtered, grouped, or
    # merged later -- drop them now rather than letting them collide on a
    # shared "" key downstream.
    df = df[df[F.TICKER] != ""].copy()

    # Preserve user notes
    current_df = pd.read_csv(CURRENT_CSV, dtype=str)
    _require_columns(current_df, [F.TICKER, F.BOUGHT, F.NOTE], "CURRENT_CSV")
    current_df[F.TICKER] = current_df[F.TICKER].map(normalize_ticker)
    current_df = current_df[current_df[F.TICKER] != ""].copy()

    # If a ticker appears more than once in CURRENT_CSV, keep whichever row
    # actually carries data. Sorting so a non-empty Bought/Note comes first
    # means a real annotation is never thrown away in favor of a blank
    # duplicate row that merely happened to appear earlier in the file.
    has_data = (
        current_df[F.BOUGHT].notna() & (current_df[F.BOUGHT].astype(str).str.strip() != "")
    ) | (
        current_df[F.NOTE].notna() & (current_df[F.NOTE].astype(str).str.strip() != "")
    )
    current_df = current_df.assign(_has_data=has_data)
    current_df = current_df.sort_values("_has_data", ascending=False)
    current_df = current_df.drop_duplicates(subset=[F.TICKER], keep="first")
    current_df = current_df.drop(columns=["_has_data"])

    # Keep only columns we want to restore later
    current_df = current_df[[F.TICKER, F.BOUGHT, F.NOTE]].copy()

    # Remove them from main dataframe if they exist
    for col in [F.BOUGHT, F.NOTE]:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Numeric coercion for the columns we filter/sort on
    df[F.AVG_VOLUME] = pd.to_numeric(df[F.AVG_VOLUME], errors="coerce")

    # Snapshot before any filtering, so rows with a non-empty "Bought" in
    # CURRENT_CSV can be recovered later even if a filter step below would
    # otherwise have dropped them.
    full_df = df.copy()

    start_n = len(df)

    # ---- Step 1: minimum avg volume (first, to shrink the dataset early) ----
    df = df[df[F.AVG_VOLUME].fillna(0) >= MIN_AVG_VOLUME].copy()
    after_volume_n = len(df)

    # ---- Step 2: drop rows with empty/missing Name ----
    df = df[df[F.NAME].notna() & (df[F.NAME].str.strip() != "")].copy()
    after_name_n = len(df)

    # ---- Step 3: exclude explicit tickers ----
    excluded_set = {normalize_ticker(t) for t in EXCLUDED_TICKERS}
    df = df[~df[F.TICKER].isin(excluded_set)].copy()
    after_exclude_n = len(df)

    # ---- Step 4: Fee lookup CSV ----
    # We currently keep the TSE-provided trust fee as-is (fast, close-enough)
    # instead of overwriting it with the manually-sourced LOOKUP_CSV fee
    # (accurate but slow to maintain). fee_lookup is still used below for
    # group dedup (Step 5). To restore fee overwriting, insert a block here
    # that builds a `fee` array from `fee_lookup` (keyed by F.TICKER)
    # and writes it into df[F.FEE].
    fee_lookup = load_lookup_fees(LOOKUP_CSV)

    # ---- Step 5: group dedup by cascading volume + lowest fee ----

    # map ticker -> group name, for quick lookup
    normalized_groups = {
        group_name: [normalize_ticker(t) for t in tickers]
        for group_name, tickers in TICKER_GROUPS.items()
    }
    ticker_to_group = {
        t: group_name for group_name, tickers in normalized_groups.items() for t in tickers
    }

    grouped_tickers = set(ticker_to_group.keys())
    df_grouped = df[df[F.TICKER].isin(grouped_tickers)].copy()
    df_ungrouped = df[~df[F.TICKER].isin(grouped_tickers)].copy()

    keep_rows = [df_ungrouped]

    for group_name, norm_tickers in normalized_groups.items():
        members = df_grouped[df_grouped[F.TICKER].isin(norm_tickers)]
        if members.empty:
            logger.info("[%s] has no members, skipped", group_name)
            continue

        # Cascade through volume thresholds.
        # At each threshold, drop any candidate not found in LOOKUP_CSV.
        # If nothing is left after dropping, retry at the next lower threshold.
        # Uses >= to stay consistent with MIN_AVG_VOLUME's >= filter above --
        # a ticker sitting exactly on a threshold now stays at that tier
        # instead of being bumped down to the next one.
        candidates = pd.DataFrame()
        used_threshold = None
        for threshold in VOLUME_CASCADE:
            vol_candidates = members[members[F.AVG_VOLUME].fillna(0) >= threshold]
            if vol_candidates.empty:
                continue
            vol_candidates = vol_candidates.copy()
            vol_candidates["_fee"] = vol_candidates[F.TICKER].map(fee_lookup)
            found = vol_candidates[vol_candidates["_fee"].notna()]
            if not found.empty:
                candidates = found
                used_threshold = threshold
                break

        if candidates.empty:
            logger.info(
                "[%s] members survived: %s | NONE found in the lookup CSV at any "
                "volume threshold, group skipped entirely",
                group_name, list(members[F.TICKER]),
            )
            continue

        candidates = candidates.sort_values(
            ["_fee", F.AVG_VOLUME], ascending=[True, False], na_position="last"
        )
        winner = candidates.iloc[[0]]
        keep_rows.append(winner.drop(columns=["_fee"]))

        logger.info(
            "[%s] members survived: %s | Threshold used: %s | "
            "Candidates found in lookup CSV: %s | Winner: %s (fee=%s)",
            group_name, list(members[F.TICKER]), used_threshold,
            list(candidates[F.TICKER]), winner[F.TICKER].iloc[0], winner["_fee"].iloc[0],
        )

    result = pd.concat(keep_rows, ignore_index=False)

    # ---- Force-preserve rows with a non-empty "Bought" ----
    # These must always survive, regardless of any filter above (volume, name,
    # explicit exclusion, or losing a group's dedup). This intentionally takes
    # priority over EXCLUDED_TICKERS too: an owned position stays visible even
    # if it's on the exclusion list. Pull the full row back in from full_df
    # (the pre-filter snapshot) if it's missing from result.
    bought_mask = current_df[F.BOUGHT].notna() & (
        current_df[F.BOUGHT].astype(str).str.strip() != ""
    )
    bought_tickers = set(current_df.loc[bought_mask, F.TICKER])
    missing_bought_tickers = bought_tickers - set(result[F.TICKER])

    if missing_bought_tickers:
        recovered = full_df[full_df[F.TICKER].isin(missing_bought_tickers)]
        if not recovered.empty:
            result = pd.concat([result, recovered], ignore_index=False)
            logger.info(
                "Force-preserved %d row(s) with non-empty Bought that would "
                "otherwise have been filtered out: %s",
                len(recovered), list(recovered[F.TICKER]),
            )
        still_missing = missing_bought_tickers - set(full_df[F.TICKER])
        if still_missing:
            logger.warning(
                "%d ticker(s) with non-empty Bought are not present in DATA_CSV "
                "at all, could not be preserved: %s",
                len(still_missing), sorted(still_missing),
            )

    # A ticker listed in more than one TICKER_GROUPS entry can independently
    # win its dedup round in each group, so guard against emitting it twice.
    result = result.drop_duplicates(subset=[F.TICKER], keep="first")

    # Keep the original DATA_CSV row order rather than sorting alphabetically.
    # keep_rows/recovered entries still carry their original index from the
    # initial read_csv (filtering/selection preserves index values), so
    # sorting on that index restores the source file's order. NOTE: this
    # guarantee depends on the index never being reset before this point --
    # if you add a step above that does df.reset_index(), this breaks silently.
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

    result.to_csv(OUTPUT_CSV, index=False)

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
