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
├── steps.py        the 10 step functions - thin glue over the modules below
├── config.py       paths, file-name rules, keyword tuples, column candidates, check thresholds
├── models.py       Grid / ColumnMap / Row / Table / Job dataclasses + number parsing and weight arithmetic
├── parsing.py      CSV / XLSX reading, last-marker lookup, header-row detection, column resolution
├── lookup.py       GLOBAL_lookup.csv access
├── checks.py       log-only sanity checks on the finished table (step 100)
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
060 isin_backfill   fill missing ISIN from GLOBAL_lookup (ticker+exchange); not for <Ticker>.csv files
070 refresh_from_isin <Ticker>.csv files only: fix Ticker via ISIN -> GLOBAL_lookup
080 exchange        fill Exchange from GLOBAL_lookup by ISIN where the row has none
082 drop_incomplete remove rows with empty Code and Name, or a Name containing a SKIP_NAME_TEXTS entry
085 weights         compute weight when missing
090 write           Ticker/Name/ISIN/Weight(%)/Exchange csv into the output folder (tickers starting with 0 get a leading ')
100 check           log-only sanity checks on tickers / ISINs / exchanges / weights (nothing changed)
```

Dependency chain: `010 -> 020 -> ... -> 100`, each step feeding the next
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
- `TICKER_FILENAME_PATTERN`: which file names count as `<Ticker>.csv` for
  step 070 (regex against the file stem, default `[0-9A-Z]{3,5}`).
- `CHECK_DOMINANT_SHARE`, `HOLDINGS_MIN_COUNT`, `WEIGHT_SUM_MAX`, `WEIGHT_SUM_MIN`, `EXCHANGE_SHORT_MAX_LEN`,
  `CHECK_MAX_LISTED`: thresholds for the step 100 checks.

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
- **Fail-fast inputs.** The lookup file must exist (checked before anything
  runs) and have `ticker` and `isin` columns (header spaces/case ignored).
  A header that resolves neither a ticker nor an ISIN column fails the file,
  as does an empty file. Two inputs with the same stem (`SPY.csv` and
  `SPY.xlsx`) would write the same output: the second one fails instead of
  overwriting the first.
- **CSV delimiter** is the one (`,` `;` tab) with the most occurrences over
  the first 10 non-empty lines, so a title line above the header can't fool it.
- **ISIN backfill (060)** fills a missing ISIN from the ticker (plus the
  row's exchange when the ticker is ambiguous). It is skipped for
  `<Ticker>.csv` files, whose tickers are the untrusted part. Ticker matching ignores
  separators ("BRK B" ~ "BRK.B"); an ambiguous ticker with no matching
  exchange is left untouched rather than guessed.
- **Step 070 only touches files named like a ticker** (`159A.csv`,
  `1306.xlsx`, ...); every other file keeps its Ticker. Each row is
  looked up by ISIN. If several lookup entries share it, the row's Exchange
  narrows them; if that still leaves several (or the row has no exchange),
  the first is used and it's printed. ISINs not found, or rows without an
  ISIN, are logged and left untouched. On a hit, the Ticker is
  overwritten; the Name is never changed by this step. The summary line
  splits refreshed / already correct / not found.
- **Per-row log lines are capped** at 10 per step per file ("no ISIN",
  "not in lookup", ...); the rest are only counted in the summary. Ambiguity
  messages ("N entries share this ISIN", "using the first") always print.
- **Every output has an `Exchange` column.** Rows keep the Exchange from
  their own source column when it has a value. Step 080 (all files) fills
  it from the lookup, by ISIN, only for rows where it is empty (several
  entries -> narrowed by the row's ticker; still several -> first; not
  found -> printed and left blank). Step 090 writes the same five columns
  for every file.
- **Step 100 only reports; it never fixes or fails a file.** "Most" means at
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
- **GlobalLookup** keeps its internal dicts as `_by_isin` / `_by_ticker` /
  `_by_ticker_loose` so they don't shadow the `by_isin()` / `by_ticker()`
  methods. It needs the columns ticker and isin (name, exchange optional);
  identical duplicate rows are collapsed in all three indexes, so a repeated
  row doesn't make a ticker look ambiguous.
