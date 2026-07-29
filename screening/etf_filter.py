import pandas as pd
from .filter_helper import normalize_ticker, load_lookup_fees
from .filter_constants import DATA_CSV, LOOKUP_CSV, MIN_AVG_VOLUME, VOLUME_CASCADE, TICKER_GROUPS, EXCLUDED_TICKERS, OUTPUT_CSV, TICKER, NAME, AVG_VOLUME

def filter_etfs():
  df = pd.read_csv(DATA_CSV, dtype=str)
  df[TICKER] = df[TICKER].map(normalize_ticker)

  # Numeric coercion for the columns we filter/sort on
  df[AVG_VOLUME] = pd.to_numeric(df[AVG_VOLUME], errors="coerce")

  start_n = len(df)

  # ---- Step 1: minimum avg volume (first, to shrink the dataset early) ----
  df = df[df[AVG_VOLUME].fillna(0) >= MIN_AVG_VOLUME].copy()
  after_volume_n = len(df)

  # ---- Step 2: drop rows with empty/missing Name ----
  df = df[df[NAME].notna() & (df[NAME].str.strip() != "")].copy()
  after_name_n = len(df)

  # ---- Step 3: exclude explicit tickers ----
  excluded_set = {normalize_ticker(t) for t in EXCLUDED_TICKERS}
  df = df[~df[TICKER].isin(excluded_set)].copy()
  after_exclude_n = len(df)

  # ---- Step 4: fix Fee using LOOKUP_CSV (ignore/keep original if ticker not found) ----
  fee_lookup = load_lookup_fees(LOOKUP_CSV)
  looked_up = df[TICKER].map(fee_lookup)
  found_mask = looked_up.notna()
  looked_up_pct = "'" + (looked_up * 100).round(4).astype(str) + '%'
  df["Fee"] = df["Fee"].where(~found_mask, looked_up_pct)
  fee_fixed_n = int(found_mask.sum())

  # ---- Step 5: group dedup by cascading volume + lowest fee ----

  # map ticker -> group name, for quick lookup
  ticker_to_group = {}
  for group_name, tickers in TICKER_GROUPS.items():
    for t in tickers:
      ticker_to_group[normalize_ticker(t)] = group_name

  grouped_tickers = set(ticker_to_group.keys())
  df_grouped = df[df[TICKER].isin(grouped_tickers)].copy()
  df_ungrouped = df[~df[TICKER].isin(grouped_tickers)].copy()

  keep_rows = [df_ungrouped]
  log_lines = []

  for group_name, tickers in TICKER_GROUPS.items():
    members = df_grouped[df_grouped[TICKER].isin(
      [normalize_ticker(t) for t in tickers]
    )]
    if members.empty:
      log_lines.append(f"[{group_name}] has no members, skipped")
      continue

    # Cascade through volume thresholds.
    # At each threshold, drop any candidate not found in LOOKUP_CSV.
    # If nothing is left after dropping, retry at the next lower threshold.
    candidates = pd.DataFrame()
    used_threshold = None
    for threshold in VOLUME_CASCADE:
      vol_candidates = members[members[AVG_VOLUME].fillna(0) > threshold]
      if vol_candidates.empty:
        continue
      vol_candidates = vol_candidates.copy()
      vol_candidates["_fee"] = vol_candidates[TICKER].map(fee_lookup)
      found = vol_candidates[vol_candidates["_fee"].notna()]
      if not found.empty:
        candidates = found
        used_threshold = threshold
        break

    if candidates.empty:
      log_lines.append(
        f"[{group_name}] members survived: {list(members[TICKER])} | "
        f"NONE found in the lookup CSV at any volume threshold, group skipped entirely"
      )
      continue

    candidates = candidates.sort_values(
      ["_fee", AVG_VOLUME], ascending=[True, False], na_position="last"
    )
    winner = candidates.iloc[[0]]
    keep_rows.append(winner.drop(columns=["_fee"]))

    log_lines.append(
      f"[{group_name}] members survived: {list(members[TICKER])} | "
      f"Threshold used: {used_threshold} | "
      f"Candidates found in lookup CSV: {list(candidates[TICKER])} | "
      f"Winner: {winner[TICKER].iloc[0]} (fee={winner['_fee'].iloc[0]})"
    )

  result = pd.concat(keep_rows, ignore_index=True)
  result = result.sort_values(TICKER).reset_index(drop=True)

  result.to_csv(OUTPUT_CSV, index=False)

  print("=== ETF filter summary ===")
  print(f"Start:                     {start_n} rows")
  print(f"After volume filter:       {after_volume_n} rows (removed {start_n - after_volume_n})")
  print(f"After empty-Name drop:     {after_name_n} rows (removed {after_volume_n - after_name_n})")
  print(f"After excluding tickers:   {after_exclude_n} rows (removed {after_name_n - after_exclude_n})")
  print(f"Fee corrected from lookup CSV for {fee_fixed_n} of {after_exclude_n} rows")
  print(f"Final (after group dedup): {len(result)} rows")
  print()
  print("=== Group decisions ===")
  for line in log_lines:
    print(line)
  print()
  print(f"Output written to {OUTPUT_CSV}")
