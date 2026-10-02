"""The pipeline steps. Each is stepNNN_name(job, cfg): it mutates job in
place and does its own bookkeeping (looping rows, counting, printing). The
reusable logic lives in the sibling modules - parsing.py, models.py,
lookup.py, checks.py - so the steps stay thin. main.py runs them
in order via run_step; nothing else calls them."""
from __future__ import annotations

import csv
import re

from .checks import check_exchanges, check_isins, check_tickers, check_weights
from .config import OUTPUT_COLUMNS, SKIP_NAME_TEXTS, TICKER_FILENAME_PATTERN
from .lookup import get_lookup
from .models import (Job, Row, Table, fill_missing_weights, format_weight,
                    parse_float, row_from_cells)
from .parsing import (find_header_row, find_last_marker_row, load_file,
                     resolve_columns)


def step010_load(job: Job, cfg) -> None:
    job.grid = load_file(job.path)
    print(f"{job.path.name}: loaded {len(job.grid.rows)} row(s)")


def step020_last_marker(job: Job, cfg) -> None:
    """If the grid contains a 'Fund Holdings as of' row, the last one starts
    the real ETF holdings: drop everything above it. No marker -> keep all."""
    grid = job.grid
    idx = find_last_marker_row(grid)
    if idx is None:
        return
    if idx:
        print(f"{job.path.name}: last 'Fund Holdings as of' at row {idx + 1} "
              f"-> dropped {idx} row(s) above")
    grid.rows = grid.rows[idx:]


def step030_header(job: Job, cfg) -> None:
    grid = job.grid
    if not grid.rows:
        raise ValueError("file is empty (no non-blank rows)")
    header_idx = find_header_row(grid)
    if header_idx is None:
        raise ValueError("no known header row found")
    if header_idx:
        print(f"{job.path.name}: header at row {header_idx + 1} "
              f"(dropped {header_idx} metadata row(s))")
    grid.rows = grid.rows[header_idx:]


def step040_columns(job: Job, cfg) -> None:
    header, body = job.grid.rows[0], job.grid.rows[1:]
    colmap = resolve_columns(header)
    resolved = colmap.resolved()
    if not resolved:
        raise ValueError("header row matched but no known columns resolved")
    if "code" not in resolved and "isin" not in resolved:
        raise ValueError(f"neither a ticker nor an ISIN column was resolved "
                         f"(only [{', '.join(resolved)}])")
    rows = [row_from_cells(cells, colmap) for cells in body]
    job.table = Table(rows=rows)
    print(f"{job.path.name}: resolved columns [{', '.join(resolved)}], {len(rows)} data row(s)")


def step085_weights(job: Job, cfg) -> None:
    filled = fill_missing_weights(job.table)
    if filled:
        print(f"{job.path.name}: computed weights for {filled} row(s)")


def step060_isin_backfill(job: Job, cfg) -> None:
    """Fill a missing ISIN from the ticker. Skipped for ticker-named files:
    their tickers are the unreliable part (step 070 repairs them from the
    ISIN), so an ISIN derived from one would be wrong."""
    if re.fullmatch(TICKER_FILENAME_PATTERN, job.path.stem):
        print(f"{job.path.name}: ticker-named file -> ISIN backfill skipped "
              f"(its tickers aren't trusted)")
        return
    lookup = get_lookup(str(cfg.lookup_path))
    filled = unmatched = no_code = 0
    for row in job.table.rows:
        if row.isin:
            continue
        if not row.code:
            no_code += 1
            continue
        hit = lookup.by_ticker(row.code, row.exchange)
        if hit and hit.isin:
            row.isin = hit.isin
            filled += 1
        else:
            unmatched += 1
    print(f"{job.path.name}: ISIN backfilled for {filled} row(s), "
          f"{unmatched} row(s) without lookup hit, {no_code} row(s) without ticker")


def step070_refresh_from_isin(job: Job, cfg) -> None:
    """Only for files named like a ticker (<Ticker>.csv): those are the ones
    with wrong Tickers. Each row is looked up by ISIN (narrowing by
    exchange if several entries share it) and its Ticker overwritten.
    Names are left as they are."""
    name = job.path.name
    if not re.fullmatch(TICKER_FILENAME_PATTERN, job.path.stem):
        print(f"{name}: file name isn't a ticker -> Ticker left as-is")
        return
    lookup = get_lookup(str(cfg.lookup_path))
    log = _RowLog(name)
    refreshed = unchanged = not_found = no_ticker = 0
    for row in job.table.rows:
        hit = _lookup_by_isin(log, row, lookup, narrow_by="exchange",
                              skipped="not refreshed")
        if hit is None:
            not_found += 1
        elif not hit.ticker:
            log.say(f"row {_who(row)}: lookup entry has no ticker -> not refreshed")
            no_ticker += 1
        elif hit.ticker != (row.code or ""):
            row.code = hit.ticker
            refreshed += 1
        else:
            unchanged += 1
    log.flush()
    print(f"{name}: tickers refreshed on {refreshed} row(s), {unchanged} already correct, "
          f"{not_found} not found (no ISIN / not in lookup), {no_ticker} lookup entry without ticker")


def step080_exchange(job: Job, cfg) -> None:
    """Every file: fill the Exchange of rows that have none from the lookup
    by ISIN (narrowing by ticker if several entries share it). Rows that
    already have an Exchange are left as they are."""
    name = job.path.name
    lookup = get_lookup(str(cfg.lookup_path))
    log = _RowLog(name)
    filled = not_found = no_exchange = 0
    for row in job.table.rows:
        if row.exchange:
            continue
        hit = _lookup_by_isin(log, row, lookup, narrow_by="ticker",
                              skipped="Exchange not filled")
        if hit is None:
            not_found += 1
        elif not hit.exchange:
            log.say(f"row {_who(row)}: lookup entry has no exchange -> Exchange not filled")
            no_exchange += 1
        else:
            row.exchange = hit.exchange
            filled += 1
    log.flush()
    print(f"{name}: Exchange filled on {filled} row(s), {not_found} not found "
          f"(no ISIN / not in lookup), {no_exchange} lookup entry without exchange")


def step082_drop_incomplete(job: Job, cfg) -> None:
    """Remove rows that aren't real holdings: both Code and Name empty, or a
    Name containing any SKIP_NAME_TEXTS entry (case sensitive substring; the
    Code is ignored in that case). Runs before the weight calculation so these
    rows can't enter its denominator."""
    rows = job.table.rows
    kept, empty, skipped = [], 0, 0
    for r in rows:
        if r.name and any(text in r.name for text in SKIP_NAME_TEXTS):
            skipped += 1
        elif not (r.code or r.name):
            empty += 1
        else:
            kept.append(r)
    if empty or skipped:
        print(f"{job.path.name}: removed {empty} row(s) with empty Code and Name, "
              f"{skipped} row(s) with a skippable Name")
        if not kept:
            print(f"{job.path.name}: WARNING all rows were removed")
    job.table.rows = kept


def step090_write(job: Job, cfg) -> None:
    """Write the five-column CSV. Tickers starting with 0 are written with a
    leading single quote (see _ticker_cell)."""
    out_path = cfg.output_dir / f"{job.path.stem}.csv"
    if out_path.exists():        # folder is wiped at start, so this is another input
        raise FileExistsError(f"{out_path.name} was already written by another input "
                              f"file with the same name stem")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(OUTPUT_COLUMNS)
        for row in job.table.rows:
            writer.writerow([_ticker_cell(row.code), row.name or "", row.isin or "",
                             _weight_cell(row.weight), row.exchange or ""])


def step100_check(job: Job, cfg) -> None:
    """Log-only sanity checks on the finished table. Findings are stored on
    the job and printed in the final summary; nothing is changed."""
    rows = job.table.rows
    job.findings = (check_tickers(rows) + check_isins(rows) + check_exchanges(rows)
                    + check_weights(rows))


# ---------------------------------------------------------------- helpers --
def _who(row: Row) -> str:
    return row.isin or row.code or row.name or "?"


def _ticker_cell(code: str | None) -> str:
    """Tickers starting with 0 get a leading single quote (e.g. 0700 -> '0700)
    so spreadsheet apps keep the 0 instead of reading the ticker as a number."""
    code = code or ""
    return "'" + code if code.startswith("0") else code


def _weight_cell(raw) -> str:
    value = parse_float(raw)
    return format_weight(value) if value is not None else ""


class _RowLog:
    """Per-row messages for one step on one file: the first PER_ROW_LIMIT are
    printed, the rest only counted (the step's summary line has the totals).
    Ambiguity messages in _lookup_by_isin bypass this and always print."""
    PER_ROW_LIMIT = 10

    def __init__(self, name: str):
        self.name = name
        self.shown = 0
        self.hidden = 0

    def say(self, message: str) -> None:
        if self.shown < self.PER_ROW_LIMIT:
            self.shown += 1
            print(f"{self.name}: {message}")
        else:
            self.hidden += 1

    def flush(self) -> None:
        if self.hidden:
            print(f"{self.name}: ... and {self.hidden} more per-row message(s) not shown")


def _lookup_by_isin(log: _RowLog, row: Row, lookup, narrow_by: str, skipped: str):
    """Row's ISIN -> ONE lookup entry, or None (why is logged). If several
    entries share the ISIN, narrow them by the row's exchange or ticker
    (narrow_by); if still several, use the first. Ambiguity is always printed;
    'no ISIN' / 'not in lookup' go through the capped log."""
    if not row.isin:
        log.say(f"row {_who(row)}: no ISIN -> {skipped}")
        return None
    hits = lookup.by_isin(row.isin)
    if not hits:
        log.say(f"row {_who(row)}: ISIN not found in lookup -> {skipped}")
        return None
    if len(hits) > 1:
        key = row.exchange if narrow_by == "exchange" else row.code
        print(f"{log.name}: row {_who(row)}: {len(hits)} lookup entries share this ISIN "
              f"-> narrowing by {narrow_by} ({key or 'none on row'})")
        narrow = (lookup.narrow_by_exchange if narrow_by == "exchange"
                  else lookup.narrow_by_ticker)
        narrowed = narrow(hits, key)
        if len(narrowed) != 1:
            print(f"{log.name}: row {_who(row)}: still {len(narrowed) or len(hits)} "
                  f"candidate(s) after {narrow_by} -> using the first")
        hits = narrowed or hits
    return hits[0]
