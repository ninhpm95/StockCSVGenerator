# ETF holdings cleanser

Reads raw CSV/XLSX/XLSM holdings from `data/ETFs` and produces one CSV per
input, with `Ticker, Name, ISIN, Weight(%), Exchange` columns.

Inputs are read-only. The entire run is built in a private sibling staging
directory. Outputs are published only if every input processes successfully;
validation findings are advisory and do not prevent publication. Failed or
empty runs retain the previous output. Publication failures restore the old
directory; if restoring it also fails, the `.cleanser-previous-*` sibling
backup remains available for recovery.

Folder moves retry briefly on permission errors to allow temporary Windows
locks to clear. If publication still fails, the terminal reports the failure
and the prepared files remain in `.cleanser-*/output` for recovery. The
traceback goes to the enabled log. Close programs using the output folder
before retrying; a retained backup path is printed if restoration also fails.

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
The console prints only processed/total counts, the output path, and failed
file names. Root `main.py` uses the project's logging configuration to write
step details and validation findings to a timestamped file in `tasks/etf_cleanser/logs/` when
`ENABLE_CHECK = True` in `config.py`. Setting it to False disables both checks
and cleanser file logging. `--verbose` adds failure tracebacks to that log.

## Pipeline

The explicit flow lives in `run.py`; steps delegate to the smaller modules.

1. Load one CSV grid or the first configured holdings sheet of a workbook.
2. Trim above the last `Fund Holdings as of` marker and detect the header.
3. Resolve columns and apply configured manual updates.
4. Drop empty/summary rows before enrichment.
5. Fill missing exchanges and unify exchange aliases.
6. Refresh tickers from existing ISIN + exchange, then backfill missing ISINs.
7. Calculate eligible missing weights and sort by descending weight.
8. Validate the final table, log diagnostics, write staged CSVs, and
   publish the complete run.

The step numbers remain stable for log recognition; cleanup (090) now runs
before enrichment, and checks (130) run before writing (120).

## Matching

`lookup.py` loads only the configured lookup (`GLOBAL_lookup.csv` by default).
Sibling files such as `JP_lookup.csv` are never discovered or loaded.
Both `ticker` and `Code` headers are accepted. Duplicate listing/ISIN identities
count once; different counters remain distinct.
Exchange aliases are applied to lookup entries as well as holdings.

Exchange filling tries the lookup first, then the country token in a
`<Ticker>_<Country>_...` filename as a last resort. The explicit
`FILENAME_COUNTRY_EXCHANGES` mapping currently supports `JP` -> `TSE`;
unmapped or ambiguous countries such as US/UK are ignored. Existing exchanges
and cash/forward/futures rows are retained. Lookup diagnostics record attempted
lookup failures even when the filename fallback subsequently fills the exchange;
the step summary reports filename fills separately.

Searches require all supplied criteria. A missing field, no match, or ambiguous
match retains the original value. For ambiguous ISIN/exchange matches, an
existing ticker may identify a unique counter. Otherwise, the limited HKEX
currency rule below may resolve it. Other ambiguities remain unresolved.

### HKEX HKD/RMB dual-counter equities

The same shares can trade on HKEX under two tickers with the same ISIN:
`03690` (MEITUAN-W, HKD) and `83690` (MEITUAN-WR, RMB), for example.
These are distinct trading counters, so neither lookup row should be deleted
as a duplicate. A lookup currency column is not required for this rule.

During ticker refresh, ISIN + HKEX must match exactly two `Stock` entries whose
numeric tickers form a `0xxxx` / `8xxxx` pair with the same last four digits.
Leading zeroes may be omitted in the lookup and are padded for this comparison.
If the existing ticker already selects one candidate, that ticker wins. If it
does not, the **holding row's trading currency** selects `0xxxx` for `HKD` or
`8xxxx` for `CNY`, `CNH`, or `RMB` (case-insensitive). For example, `380A.csv`
can replace its source identifier `BGJW376` with `03690` using its ISIN and HKD
currency. Each successful currency tie-break is recorded in the run log.

Missing/other currencies, extra candidates, mismatched suffixes, ETFs, bonds,
and other exchanges remain ambiguous. This does not infer trading currency
from the ISIN country, fund currency, or filename; nor does it apply a general
"all 8-prefixed codes mean RMB" rule. Nonstandard counter pairs are left for
manual review. The lookup data and holdings currency are not modified.

Reference: [HKEX Dual Counter Model FAQ, Trading question 11](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/HKDRMB-Dual-Counter/Dual-Counter-Model-FAQ_E.pdf).

HKEX numeric ticker padding is ignored during matching (`1299` = `01299`).
Leading zeroes on other exchanges remain significant. SET punctuation is
retained to distinguish NVDRs from separate instruments; other ticker
separators are ignored. Lookup caches are reset before each run.

Ticker refresh remains restricted to ticker-named CSV files by default;
`REFRESH_TICKERS_ALL_FILES` expands its scope. `EXCLUDE_RULES` still applies.
Manual update rules run first and later enrichment can overwrite their values.

`instruments.py` conservatively identifies cash, forwards, futures and bonds.
Cash requires known currency codes (including TWD) with currency/FX-pair names,
currency cash labels, or `CASH COLLATERAL ...` rows without a security ISIN.
Currency identifiers are distinguished from stock tickers:
`COP,CONOCOPHILLIPS` remains a security
even without an ISIN, while `COP,COP CASH` is cash. A currency-like ticker alone
does not establish cash. Cash/forward identifiers in the ISIN column are
supported. Compact futures use an explicit list of
provider roots plus a valid YYMM expiry, e.g. `IFSNIFTY2608` and `SPEMINI2609`;
this includes MAXIS futures `JPXNIKKEI400 F 2609` and `TSEREIT 2609`, which
must not receive TSE through the filename fallback. Specific Global X contract
roots with month-name expiries (e.g. `NYSE FANG+ Idx Fu Sep26`) are supported,
as are currency-pair futures on NGC. Provider forward names such as
`FWRDUSDJPY01` are recognized even when the ticker and ISIN cells are blank.
Arbitrary names ending in numbers are not treated as futures. A security ISIN
protects listed names from futures classification. A general `FUTURE`/`FUTURES`
name match also requires a contract expiry, so "Future Corporation", "Future
Corp", and "POSCO Future M Ltd" stay eligible even without an ISIN.
Bond rules recognize Treasury instruments, Japan government bill codes
`JTDB`/`JPGV` with matching government names, and provider `B.` identifier
formats with an ISIN (see the patterns in `instruments.py`).

These recognized non-equity instruments are retained, with their weights,
but excluded from equity listing lookups and identifier coverage. Futures and
bonds are excluded from equity ticker-format checks; bond ISIN format/checksum
validation still runs. Unrecognized instruments remain eligible for lookup;
add specific observed patterns rather than broad words like "cash" or "future"
that can occur in company/fund names. Missing-value
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

All diagnostics are written to the timestamped run log in
`tasks/etf_cleanser/logs/`; no diagnostic files are created in the output folder.
The log includes complete missing-field/no-match/ambiguity records with step,
final identifiers, weight, lookup details, and final output row numbers.
It also includes eligible row counts and ticker/ISIN coverage grouped by file
and exchange, followed by total missing identifier counts.
Lookup issue counts describe attempted searches, not distinct holdings.
Each issue is marked `resolved_later` when its missing exchange/ISIN has since
been filled (e.g. by filename fallback), or `unresolved` otherwise. Both totals
are included in the lookup summary; final coverage describes the output state.

Lookup records appear once, grouped by file and step at `DEBUG` level, with
final output line numbers. Routine per-file transformations and complete
coverage records are also `DEBUG`; no-op messages are omitted. Cleanup samples
are capped at ten per file. `INFO` records show stage start/completion counts
and elapsed times, reference paths, and run/publication results. Validation
findings and identifier gaps use `WARNING`; processing failures use `ERROR`.
Every level is retained in the same log by the root entry point; `--verbose`
still controls failure tracebacks, not whether DEBUG detail is written.

The closing overview follows the DEBUG records and includes lookup attempt
totals, resolved-later counts, final identifier totals, the ten files with the
most missing fields, and validation findings. Missing fields on a row are
counted separately. Times use `HH:MM:SS.mmm` with aligned level labels; the log
filename supplies the date. Search for `LOOKUPS`, `IDENTIFIERS`, `VALIDATION`,
or `RUN COMPLETE` to jump to summaries, or filter out `| DEBUG` for an overview.
The log's closing summary counts files without validation findings, files requiring
review, and processing failures. Identifier coverage is reported separately;
a file without validation findings may still have incomplete lookup coverage.

Folder containment, duplicate output stems, and reference-file schemas are
checked before staging. Input/output folders must be disjoint, and output
must not contain reference files or be a symbolic link. On successful
publication, stale outputs from the previous run are removed with the old
directory backup; no partial run replaces the last completed output.
