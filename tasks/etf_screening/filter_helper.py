import re
import unicodedata
import pandas as pd
from .constants import Fields as F

# Values that mean "no ticker" even though they aren't NaN -- e.g. a cell
# that literally contains the text "nan"/"None"/"null" (common when a CSV
# passed through an earlier str(NaN) somewhere upstream). Compared
# case-insensitively.
_MISSING_TICKER_STRINGS = {"nan", "none", "null"}


def _require_columns(df, columns, label):
    """Raise a clear error if any of `columns` is missing from `df`."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{label} is missing required column(s): {missing}")


def normalize_ticker(t) -> str:
    """Normalize a ticker value to a stripped string.

    NaN/missing values become "" rather than the literal string "nan". If we
    let NaN become "nan", every row with a missing ticker collapses onto the
    same fake key, which can cause spurious many-to-one merges (e.g. against
    current_df) and corrupt or duplicate rows in the output.

    Also treats a cell that literally *contains* the text "nan"/"None"/"null"
    the same way, so a stray stringified-NaN upstream doesn't get treated as
    a real ticker.
    """
    if pd.isna(t):
        return ""
    s = str(t).strip()
    if s.lower() in _MISSING_TICKER_STRINGS:
        return ""
    return s


def parse_number(raw):
    """Convert fee-like values to a canonical fraction, e.g. 0.15% -> 0.0015.

    Handles plain numbers ("0.15"), percent-suffixed numbers ("0.15%",
    "1.26%"), a leading quote some spreadsheet exports add ("'0.09%"), and
    footnote markers like "(*1)". Returns None if the value can't be parsed.

    ASSUMPTION (please verify against your real data): bare, non-percent
    numbers are treated as being on the same percent scale as the
    percent-suffixed ones -- i.e. "0.15" is treated as meaning "0.15%", same
    as an explicit "0.15%", and both are divided by 100. Previously "0.15"
    and "0.15%" parsed to values 100x apart (0.15 vs 0.0015), which would
    silently corrupt any fee comparison (e.g. the "lowest fee wins" sort in
    run.py's group dedup) if the source CSV mixes both forms.

    If bare numbers in your data are actually already-decimal fractions
    (e.g. a bare 0.0015 meaning 0.15%, not a bare 0.15 meaning 0.15%),
    change the return statement below to only divide when has_percent is
    True.

    Also NFKC-normalizes before parsing, so full-width forms common in
    Japanese-sourced spreadsheets (e.g. "0.15％" or full-width digits like
    "０.15%") parse the same as their ASCII equivalents instead of silently
    failing float() and returning None -- which, in run.py's Step 5 cascade,
    would look identical to "ticker not found in lookup" and could quietly
    knock a real candidate out of group dedup.
    """
    if pd.isna(raw):
        return None
    s = unicodedata.normalize("NFKC", str(raw)).strip().lstrip("'")
    s = re.sub(r"\(\*\d+\)", "", s).strip()
    if s.endswith("%"):
        s = s[:-1]
    try:
        value = float(s)
    except ValueError:
        return None
    return value / 100


def load_lookup_fees(lookup_path, encoding="utf-8") -> dict:
    lookup = pd.read_csv(lookup_path, dtype=str, encoding=encoding)
    _require_columns(lookup, [F.TICKER, F.FEE], f"Lookup CSV at {lookup_path}")

    lookup[F.TICKER] = lookup[F.TICKER].map(normalize_ticker)
    lookup[F.FEE] = lookup[F.FEE].map(parse_number)

    # Drop rows with no usable ticker (now "" instead of "nan" -- see
    # normalize_ticker) before they can collide with each other as a shared
    # fake key.
    lookup = lookup[lookup[F.TICKER] != ""]

    # If a ticker appears more than once, keep the first occurrence
    # (dict(zip(...)) would otherwise silently keep the last one).
    lookup = lookup.drop_duplicates(subset=[F.TICKER], keep="first")
    return dict(zip(lookup[F.TICKER], lookup[F.FEE]))


def dedup_groups(df, normalized_groups, fee_lookup, cascade, logger=None):
    """Split `df` into ungrouped rows + one winner per ticker group.

    `normalized_groups` is {group_name: [normalized_ticker, ...]} (already
    run through normalize_ticker -- see TICKER_GROUPS in constants.py).
    Note: if the same ticker appears in more than one group, later groups
    (in dict iteration order) take precedence for membership purposes --
    this matches the original inline implementation. The final
    drop_duplicates(subset=F.TICKER) in run() is what actually guards
    against a ticker being emitted twice if it independently wins more
    than one group.

    For each group, cascades through descending volume thresholds
    (`cascade`, must be sorted descending) looking for the first threshold
    where at least one member has a fee in `fee_lookup`; among those,
    picks the lowest fee (ties broken by higher volume, then by ticker for
    determinism). Returns a single concatenated, non-deduped DataFrame
    (ungrouped rows + one winner row per group that found any match) --
    callers should still drop_duplicates on F.TICKER afterward.

    Pulled out of run() so the cascade/dedup logic can be unit-tested
    without touching disk.
    """
    ticker_to_group = {
        t: group_name
        for group_name, tickers in normalized_groups.items()
        for t in tickers
    }
    grouped_tickers = set(ticker_to_group.keys())
    df_grouped = df[df[F.TICKER].isin(grouped_tickers)].copy()
    df_ungrouped = df[~df[F.TICKER].isin(grouped_tickers)].copy()

    keep_rows = [df_ungrouped]

    for group_name, norm_tickers in normalized_groups.items():
        members = df_grouped[df_grouped[F.TICKER].isin(norm_tickers)]
        if members.empty:
            if logger:
                logger.info("[%s] has no members, skipped", group_name)
            continue

        candidates = pd.DataFrame()
        used_threshold = None
        for threshold in cascade:
            vol_candidates = members[members[F.NOTIONAL_VOLUME].fillna(0) >= threshold]
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
            if logger:
                logger.info(
                    "[%s] members survived: %s | NONE found in the lookup CSV "
                    "at any volume threshold, group skipped entirely",
                    group_name, list(members[F.TICKER]),
                )
            continue

        # Explicit F.TICKER tiebreak makes the winner deterministic even
        # when two candidates share both fee and volume.
        candidates = candidates.sort_values(
            ["_fee", F.NOTIONAL_VOLUME, F.TICKER],
            ascending=[True, False, True],
            na_position="last",
            kind="stable",
        )
        winner = candidates.iloc[[0]]
        keep_rows.append(winner.drop(columns=["_fee"]))

        if logger:
            logger.info(
                "[%s] members survived: %s | Threshold used: %s | "
                "Candidates found in lookup CSV: %s | Winner: %s (fee=%s)",
                group_name, list(members[F.TICKER]), used_threshold,
                list(candidates[F.TICKER]), winner[F.TICKER].iloc[0], winner["_fee"].iloc[0],
            )

    return pd.concat(keep_rows, ignore_index=False)


def force_preserve_bought(result, full_df, current_df, logger=None):
    """Ensure every ticker with a non-empty Bought in `current_df` survives.

    Pulls missing rows back in from `full_df` (a pre-filter snapshot).
    Returns (result_with_recovered_rows, still_missing_tickers) -- the
    second value lists Bought tickers that aren't in full_df at all and so
    couldn't be recovered.
    """
    bought_mask = current_df[F.BOUGHT].notna() & (
        current_df[F.BOUGHT].astype(str).str.strip() != ""
    )
    bought_tickers = set(current_df.loc[bought_mask, F.TICKER])
    missing_bought_tickers = bought_tickers - set(result[F.TICKER])

    still_missing = set()
    if missing_bought_tickers:
        recovered = full_df[full_df[F.TICKER].isin(missing_bought_tickers)]
        if not recovered.empty:
            result = pd.concat([result, recovered], ignore_index=False)
            if logger:
                logger.info(
                    "Force-preserved %d row(s) with non-empty Bought that would "
                    "otherwise have been filtered out: %s",
                    len(recovered), list(recovered[F.TICKER]),
                )
        still_missing = missing_bought_tickers - set(full_df[F.TICKER])
        if still_missing and logger:
            logger.warning(
                "%d ticker(s) with non-empty Bought are not present in DATA_CSV "
                "at all, could not be preserved: %s",
                len(still_missing), sorted(still_missing),
            )

    return result, still_missing
