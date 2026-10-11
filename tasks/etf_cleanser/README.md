# ETF holdings cleanser

Reads raw ETF holdings files (CSV + XLSX/XLSM) from `root/data/ETFs` and writes one
tidy CSV per file into `root/data/cleansed_ETFs` with columns
`Ticker, Name, ISIN, Weight(%), Exchange`.

Inputs are opened read-only and never modified. Once there is at least one
input file, the output folder is wiped and rebuilt before any file is
processed, so a run where every file fails leaves no stale output. With an
empty input folder the run stops early and the output folder is left alone
(a mistyped or unmounted input path shouldn't destroy the last good output).

## Layout

```
etf_cleanser/
├── main.py         entry point: plumbing (folder checks, discovery, run_step, summary) + the flow in main()
├── steps.py        the 12 step functions - thin glue over the modules below
├── config.py       paths, file-name rules, keyword tuples, column candidates, check thresholds
├── models.py       Grid / ColumnMap / Row / Table / Job dataclasses + number parsing and weight arithmetic
├── parsing.py      CSV / XLSX reading, last-marker lookup, header-row detection, column resolution
├── lookup.py       GLOBAL_lookup.csv access
├── checks.py       log-only sanity checks on the finished table (step 130)
├── requirements.txt
└── README.md
```

`main()` only contains flow - no business logic. Each step in `steps.py`
is a few lines that call into one focused module.

## Run

```
pip install -r requirements.txt
python main.py                # add --input/--output/--lookup as needed; --verbose prints tracebacks
```

Progress is printed to the terminal. To keep a copy: `python main.py | tee run.log`.
Exit code is 1 if any file failed, 0 otherwise.

## The steps

```
010 load            read csv, or the 保有明細 sheet of an xlsx, into one raw grid
020 last_marker     keep only rows from the last 'Fund Holdings as of' down
030 header          find header row, drop metadata above it
040 columns         resolve candidate columns -> Table
050 refresh_from_isin correct Ticker via existing ISIN + Exchange -> GLOBAL_lookup (<Ticker>.csv only)
060 isin_backfill   fill missing ISIN from GLOBAL_lookup (ticker+exchange; all files)
070 exchange        fill missing Exchange from GLOBAL_lookup using Ticker + ISIN
080 exchange_short_form convert Exchange values to configured short names
090 drop_incomplete remove rows with empty Code and Name, or a Name containing a SKIP_NAME_TEXTS entry
100 weights         compute weight when missing
110 sort_by_weight  sort holdings by descending weight (missing/invalid last)
120 write           Ticker/Name/ISIN/Weight(%)/Exchange csv into the output folder (tickers starting with 0 get a leading ')
130 check           log-only sanity checks on tickers / ISINs / exchanges / weights (nothing changed)
```

Dependency chain: `010 -> 020 -> ... -> 130`, each step feeding the next
(050 onward work on `job.table`, which step 040 creates). Steps are numbered
010/020/... so more can be slotted in later. `main.py` is literally the flow:

```python
run_step(steps.step010_load, jobs, cfg)
run_step(steps.step020_last_marker, jobs, cfg)
...
```

Add, remove, comment out or reorder a step by editing those lines.
`run_step()` (in `main.py`) runs a step over every job and catches
failures: a failing step marks that file as failed and later steps skip it,
but the other files carry on.

## Knobs (config.py)

- `ENABLE_REFRESH_FROM_ISIN`, `ENABLE_ISIN_BACKFILL`, `ENABLE_EXCHANGE`,
  `ENABLE_EXCHANGE_SHORT_FORM`, `ENABLE_WEIGHTS`, `ENABLE_SORT_BY_WEIGHT`, `ENABLE_CHECK`: set any to `False`
  to skip steps 050, 060, 070, 080, 100, 110, or 130 respectively. Step 070 defaults
  to `False`; the others default to `True`. Sorting puts the highest numeric weight first, keeps equal weights
  in their source order, and puts missing/invalid weights last. Disabling it
  preserves the row order. Sorting also works with weight calculation disabled.
  Disabled steps
  are reported as skipped; the remaining steps keep their order. When checks
  are disabled, the summary reports files processed rather than checks passed.
  The lookup file is only required if at least one of the three lookup steps
  (050, 060, 070) is enabled.
- `HOLDINGS_SHEET`: list of candidate Excel sheet names, tried left to right
  (default `["保有明細"]`). The first one found is read and the rest are
  ignored; if none is found the file fails. Add another provider's sheet by
  appending to the list, e.g. `["保有明細", "Holding sheet"]`.
- `HOLDINGS_MARK`: the "Fund Holdings as of" marker text (case-sensitive,
  matched anywhere in a cell).
- `*_COLUMN_CANDIDATES`: matching is case-sensitive, anywhere in a cell, but full matching is prioritized.
- `HEADER_KEYWORD_COMBINATIONS`: matched against the raw cells as
  case-sensitive substrings, so no whitespace/bracket normalisation here
  (a header cell with a double space won't match a single-space keyword).
- `COLUMN_RESOLUTION_ORDER`: order in which fields (code, name, isin, ...)
  claim header columns; a column can belong to only one field, so earlier
  fields win. Fields not listed are never resolved. (The unused `region`
  field was removed.)
- `CHECK_DOMINANT_SHARE`, `HOLDINGS_MIN_COUNT`, `WEIGHT_SUM_MAX`, `WEIGHT_SUM_MIN`, `EXCHANGE_SHORT_MAX_LEN`,
  `CHECK_MAX_LISTED`: thresholds for the step 130 checks.

## Behaviour worth knowing

- **Every file is one grid.** A CSV is one grid; for a workbook only the
  first matching sheet from `HOLDINGS_SHEET` (default `保有明細`) is read at load
  time and all other sheets are ignored, so steps 020 onward treat both file
  types identically. A workbook with none of the listed sheets, a file with no recognisable header row, or a header with no known
  columns fails with a clear error (other files still run).
- **"Fund Holdings as of" is just a trim.** The last row containing the
  marker (any cell, case-sensitive) starts the real ETF holdings; every row
  above it is dropped, and with no marker the file is kept whole. The header
  step then finds the header below the marker as usual.
- **Nothing is deleted.** Total/NAV/footnote rows (合計, Total Net Assets,
  ※ footnotes) and placeholder cell text (`-`, `nan`, `null`) are not stripped:
  whatever is below the header flows straight to the output. Worth checking a
  few output files for stray Total/footnote rows. One guard limit the damage:
  numbers that are placeholders (`nan`, `inf`, `-`) parse as "no value".
- **Fail-fast inputs.** When any lookup step is enabled, the lookup file must
  exist (checked before anything runs) and have `ticker` and `isin` columns
  (header spaces/case ignored).
  A header that resolves neither a ticker nor an ISIN column fails the file,
  as does an empty file. Two inputs with the same stem (`SPY.csv` and
  `SPY.xlsx`) would write the same output: the second one fails instead of
  overwriting the first.
- **CSV delimiter** is the one (`,` `;` tab) with the most occurrences over
  the first 10 non-empty lines, so a title line above the header can't fool it.
- `TICKER_FILENAME_PATTERN`: the filename stem pattern for step 050
  (default `[0-9A-Z]{3,5}`). Only CSV files matching it are corrected.
- **Ticker correction (050)** runs only on `<Ticker>.csv` files, before ISIN backfill.
  Other holdings files retain their source tickers.
  Each row requires a match on both existing ISIN and Exchange; without a unique match, the row is
  logged and its ticker is retained. Rows matching any `EXCLUDE_RULES`
  entry retain their ticker (default: Exchange TSE or Currency JPY,
  case-insensitive). On a unique hit the Ticker is corrected; Name, ISIN,
  and Exchange are retained. Rows without either search field or a lookup hit cannot be
  corrected and are logged. The summary splits refreshed / already correct /
  unresolved / lookup entry without ticker.
- **ISIN backfill (060)** runs on every file, after ticker correction, and
  fills only missing ISINs using both Ticker and Exchange. Both fields must
  be present and identify exactly one lookup entry; missing
  fields, exchange mismatches, and multiple matches leave the row untouched.
  Existing ISINs are retained. Ticker matching ignores separators
  ("BRK B" ~ "BRK.B"). A row without an original ISIN uses its source ticker for
  backfill because step 050 could not independently correct that ticker.
- **Per-row log lines are capped** at 10 per step per file ("no ISIN",
  "not in lookup", ...); the rest are only counted in the summary. Ambiguity
  messages ("N lookup entries match") always print.
- **Every output has an `Exchange` column.** Rows keep the Exchange from
  their own source column when it has a value. Step 070 (all files) fills
  it from the lookup using both Ticker and ISIN, only for rows where it is empty.
  Missing search fields, no match, or multiple matches are logged and leave it blank.
  Step 070 is disabled by default. Step 080 independently converts existing
  and filled exchange names to configured short forms using `exchanges.csv`;
  unknown names and empty values are retained. It is enabled by default and
  can run while step 070 is disabled. Step 120 writes the same five columns
  for every file.
- **Step 130 only reports; it never fixes or fails a file.** "Most" means at
  least 90% of the rows but not all of them, so a uniform file is silent.
  Checks: (1) ticker length - with at least 10 tickers, if one length
  covers most of them (e.g. 4 chars), the tickers of any other length are
  listed; (2) ticker kind (also needs 10 tickers) - if most tickers are text
  only (`AAPL`, `BRK.B`, `BRK-B`), those containing digits are listed; if
  most are digits-only or digits plus a single letter (`7021`, `586A`), the
  ones that aren't are listed; (3) ISIN country (also needs 10 ISINs) - most share a 2-letter prefix, a few don't; (4) Exchange
  (all files, no minimum count) - values over 10 characters aren't a short form
  (e.g. "Tokyo Stock Exchange" vs TSE). (5) Weights (already in percent, 20 = 20%) - the file is flagged if they add up to more than
  `WEIGHT_SUM_MAX` (100) or less than `WEIGHT_SUM_MIN` (60), every holding with a negative weight is listed,
  and rows with no usable weight are listed separately (so a gap isn't mistaken for a low total).
  All non-alphanumeric characters (`.`, `-`, spaces...) are ignored when measuring tickers.
  Rows are reported as output-file line numbers (header = line 1); at most 3
  are listed per finding, then "... and N more".
- **`GlobalLookup.find(**criteria)`** searches by any combination of one or more
  columns (`ticker`, `isin`, `exchange`, `name`, `asset_type`). All supplied
  values must be present and match. It returns one entry or `None`, and raises
  `AmbiguousLookupError` for multiple matches; steps 050/060/070 catch that
  error, log it, and skip the update. Ticker separators and exchange aliases
  are normalised. ISIN and ticker indexes restrict the search candidates.
  The lookup needs ticker and isin columns; identical duplicate records count once.
