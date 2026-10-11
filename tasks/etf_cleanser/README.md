# ETF holdings cleanser

Reads raw CSV/XLSX/XLSM holdings from `data/ETFs` and produces one CSV per
input, with `Ticker, Name, ISIN, Weight(%), Exchange` columns.

Inputs are read-only. The entire run is built in a private sibling staging
directory. Outputs are published only if every input processes successfully;
validation findings are advisory and do not prevent publication. Failed or
empty runs retain the previous output. Publication failures restore the old
directory; if restoring it also fails, the `.cleanser-previous-*` sibling
backup remains available for recovery.

## Run

From the project root, select the cleanser mode in `constants.py` and run:

```powershell
python main.py
```

The root entry point forwards command-line options to the cleanser:

```powershell
python main.py --input data/ETFs --output data/cleansed_ETFs --verbose
python main.py --lookup data/stocks/GLOBAL_lookup.csv
```

`--exchanges` overrides the exchange-alias CSV. Exit status is 1 on a processing
failure, 0 on a successfully processed run (including advisory findings).
Progress prints to the console and also uses the project's logging handler;
set `ENABLE_LOG_FILE = True` in root `main.py` to retain a timestamped log.

## Pipeline

The explicit flow lives in `run.py`; steps delegate to the smaller modules.

1. Load one CSV grid or the first configured holdings sheet of a workbook.
2. Trim above the last `Fund Holdings as of` marker and detect the header.
3. Resolve columns and apply configured manual updates.
4. Drop empty/summary rows before enrichment.
5. Fill missing exchanges and unify exchange aliases.
6. Refresh tickers from existing ISIN + exchange, then backfill missing ISINs.
7. Calculate eligible missing weights and sort by descending weight.
8. Validate the final table, write staged CSVs and diagnostic reports, and
   publish the complete run.

The step numbers remain stable for log recognition; cleanup (090) now runs
before enrichment, and checks (130) run before writing (120).

## Matching

`lookup.py` loads only the configured lookup (`GLOBAL_lookup.csv` by default).
Sibling files such as `JP_lookup.csv` are never discovered or loaded.
Both `ticker` and `Code` headers are accepted. Duplicate listing/ISIN identities
count once; different counters remain distinct.
Exchange aliases are applied to lookup entries as well as holdings.

Searches require all supplied criteria. A missing field, no match, or ambiguous
match retains the original value. For ambiguous ISIN/exchange matches, an
existing ticker may identify a unique counter. Without that evidence, the
ambiguity remains unresolved. Currency is not inferred for unknown counters.

HKEX numeric ticker padding is ignored during matching (`1299` = `01299`).
Leading zeroes on other exchanges remain significant. SET punctuation is
retained to distinguish NVDRs from separate instruments; other ticker
separators are ignored. Lookup caches are reset before each run.

Ticker refresh remains restricted to ticker-named CSV files by default;
`REFRESH_TICKERS_ALL_FILES` expands its scope. `EXCLUDE_RULES` still applies.
Manual update rules run first and later enrichment can overwrite their values.

`instruments.py` conservatively identifies cash, forwards, futures and Treasury
instruments. Cash/forward identifiers in the ISIN column are supported. Cash,
forwards and futures are retained but excluded from listing lookups; futures
and bonds are also excluded from equity ticker-format checks. Missing-value
markers in identifier/numeric columns (`-`, `nan`, `null`, `none`, `n/a`) become
empty values.

## Weights and validation

Provider percentages are retained. Missing weights use the existing formula:
row value divided by the sum of computable row values, times 100. An explicit
`Market Value/Jpy` column takes priority; otherwise shares times price falls
back to valuation. Currency labels alone do not block calculation, since they
do not establish the basis of provider prices. Rows without a computable value
remain blank; supplied weights are never overwritten. This calculation does
not perform currency conversion. Existing weight totals are not automatically
normalized to 100%.

Checks validate known TSE/HKEX ticker formats and ISIN structure/checksums,
instead of comparing ticker styles or ISIN countries across a whole fund.
Other market formats are retained without speculative format restrictions.
Long unknown exchange aliases, unusable weights, and out-of-range weight
sums are still reported. Configure verified fund-specific expected exposure
in `WEIGHT_SUM_EXPECTED`; leveraged-fund exceptions are not inferred from
an observed high total alone.

## Diagnostics

Each published run includes a `diagnostics/` subdirectory:

- `lookup_issues.csv`: complete missing-field/no-match/ambiguity records,
  with step, identifiers, weight, details, and final output row numbers.
- `identifier_coverage.csv`: eligible row counts and ticker/ISIN coverage,
  grouped by file and exchange.

Console row messages are capped at ten per step/file, including ambiguities.
The closing summary counts files without validation findings, files requiring
review, and processing failures. Identifier coverage is reported separately;
a file without validation findings may still have incomplete lookup coverage.

Folder containment, duplicate output stems, and reference-file schemas are
checked before staging. Input/output folders must be disjoint, and output
must not contain reference files or be a symbolic link. On successful
publication, stale outputs from the previous run are removed with the old
directory backup; no partial run replaces the last completed output.
