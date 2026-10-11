"""The pipeline steps. Each is stepNNN_name(job, cfg): it mutates job in
place and does its own bookkeeping (looping rows, counting, printing). The
reusable logic lives in the sibling modules - parsing.py, models.py,
lookup.py, checks.py - so the steps stay thin. run.py runs them
in order via run_step; nothing else calls them."""
from __future__ import annotations

import csv
import re

from .checks import check_exchanges, check_isins, check_tickers, check_weights
from .config import (OUTPUT_COLUMNS, SKIP_NAME_TEXTS, CODE_TO_CHECK_NAME_TO_SKIP,
                     TICKER_FILENAME_PATTERN, EXCLUDE_RULES)
from .lookup import AmbiguousLookupError, get_lookup, get_exchange_names
from .manual_updates import apply_manual_updates
from .instruments import needs_listing_lookup
from .reporting import emit
from .models import (Job, Row, Table, fill_missing_weights, format_weight,
                    parse_float, row_from_cells)
from .parsing import (find_header_row, find_last_marker_row, load_file,
                     resolve_columns)


def step010_load(job: Job, cfg) -> None:
    job.grid = load_file(job.path)


def step020_last_marker(job: Job, cfg) -> None:
    """If the grid contains a 'Fund Holdings as of' row, the last one starts
    the real ETF holdings: drop everything above it. No marker -> keep all."""
    grid = job.grid
    idx = find_last_marker_row(grid)
    if idx is None:
        return
    if idx:
        emit(f"{job.path.name}: last 'Fund Holdings as of' at row {idx + 1} "
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
        emit(f"{job.path.name}: header at row {header_idx + 1} "
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
    common_currency = ("JPY" if colmap.valuation is not None
                       and "Market Value/Jpy" in header[colmap.valuation] else None)
    job.table = Table(rows=rows, valuation_currency=common_currency)
    emit(f"{job.path.name}: resolved columns [{', '.join(resolved)}], {len(rows)} data row(s)")


def step041_manual_updates(job: Job, cfg) -> None:
    """Apply explicit manual corrections immediately after columns are resolved."""
    changed = apply_manual_updates(job.table.rows, cfg.manual_update_rules)
    emit(f"{job.path.name}: manual updates changed {changed} row(s)")


def step050_exchange(job: Job, cfg) -> None:
    """Every file: fill the Exchange of rows that have none from the lookup
    by Ticker + ISIN. Rows that
    already have an Exchange are left as they are."""
    name = job.path.name
    lookup = get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    log = _RowLog(name, job, "exchange")
    filled = not_found = no_exchange = 0
    for row in job.table.rows:
        if row.exchange or not needs_listing_lookup(row):
            continue
        hit = _lookup_row(log, row, lookup, "Exchange not filled",
                          ticker=row.code, isin=row.isin)
        if hit is None:
            not_found += 1
        elif not hit.exchange:
            job.lookup_issues.append(("exchange", "lookup_missing_value", row, "exchange"))
            log.say(f"row {_who(row)}: lookup entry has no exchange -> Exchange not filled")
            no_exchange += 1
        else:
            row.exchange = hit.exchange
            filled += 1
    log.flush()

    emit(f"{name}: Exchange filled on {filled} row(s), {not_found} not found "
          f"(missing search fields / no match / ambiguous), {no_exchange} lookup entry without exchange")


def step060_unify_exchange_names(job: Job, cfg) -> None:
    """Unify existing and filled Exchange values to lookup names via exchanges.csv."""
    exchange_names = get_exchange_names(str(cfg.exchanges_path))
    converted = 0
    for row in job.table.rows:
        if not row.exchange:
            continue
        unified = exchange_names.get(row.exchange.strip().casefold())
        if unified and unified != row.exchange:
            row.exchange = unified
            converted += 1

    emit(f"{job.path.name}: {converted} Exchange value(s) unified to lookup name")


def step070_refresh_from_isin(job: Job, cfg) -> None:
    """Correct tickers before ISIN backfill; default scope is ticker-named CSVs.
    cfg.refresh_tickers_all_files expands the scope to all supported files.
    Require existing ISIN + Exchange to match uniquely; leave unresolved
    rows untouched. Names, ISINs and exchanges are retained."""
    name = job.path.name
    if (not cfg.refresh_tickers_all_files
            and (job.path.suffix.lower() != ".csv"
                 or not re.fullmatch(TICKER_FILENAME_PATTERN, job.path.stem))):
        emit(f"{name}: not a <Ticker>.csv file -> Ticker left as-is")
        return
    lookup = get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    log = _RowLog(name, job, "ticker")
    refreshed = unchanged = not_found = no_ticker = excluded = 0
    for row in job.table.rows:
        if _is_excluded(row) or not needs_listing_lookup(row):
            excluded += 1
            continue
        # Prefer an existing ticker within ambiguous ISIN/exchange candidates.
        # Unknown counters remain ambiguous rather than choosing arbitrarily.
        hit = _lookup_row(log, row, lookup, "not refreshed",
                          preferred_ticker=row.code, isin=row.isin, exchange=row.exchange)
        if hit is None:
            not_found += 1
        elif not hit.ticker:
            job.lookup_issues.append(("ticker", "lookup_missing_value", row, "ticker"))
            log.say(f"row {_who(row)}: lookup entry has no ticker -> not refreshed")
            no_ticker += 1
        elif hit.ticker != (row.code or ""):
            row.code = hit.ticker
            refreshed += 1
        else:
            unchanged += 1
    log.flush()
    emit(f"{name}: tickers refreshed on {refreshed} row(s), {unchanged} already correct, "
          f"{not_found} unresolved (missing search fields / no match / ambiguous), "
          f"{no_ticker} lookup entry without ticker, {excluded} excluded")


def step080_isin_backfill(job: Job, cfg) -> None:
    """Every file: fill missing ISINs from ticker + exchange after correction.
    Existing ISINs are retained."""
    lookup = get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    log = _RowLog(job.path.name, job, "isin")
    filled = unmatched = no_code = 0
    for row in job.table.rows:
        if row.isin or not needs_listing_lookup(row):
            continue
        if not row.code:
            job.lookup_issues.append(("isin", "missing_fields", row, "ticker"))
            no_code += 1
            continue
        hit = _lookup_row(log, row, lookup, "ISIN not filled",
                          ticker=row.code, exchange=row.exchange)
        if hit and hit.isin:
            row.isin = hit.isin
            filled += 1
        else:
            if hit:
                job.lookup_issues.append(("isin", "lookup_missing_value", row, "isin"))
            unmatched += 1
    log.flush()
    emit(f"{job.path.name}: ISIN backfilled for {filled} row(s), "
          f"{unmatched} row(s) without lookup hit, {no_code} row(s) without ticker")


def step090_drop_incomplete(job: Job, cfg) -> None:
    """Remove rows that aren't real holdings:
    both Code and Name empty,
    or a Name containing any SKIP_NAME_TEXTS entry when the Code is empty or one of CODE_TO_CHECK_NAME_TO_SKIP (case sensitive substring)."""
    log = _RowLog(job.path.name)
    kept, empty, skipped = [], 0, 0
    for r in job.table.rows:
        code = (r.code or "").strip()
        matched = next((t for t in SKIP_NAME_TEXTS if r.name and t in r.name), None)
        if code in CODE_TO_CHECK_NAME_TO_SKIP and matched:
            skipped += 1
            log.say(f"dropped by name: '{r.name}' (matched '{matched}')")
        elif not (r.code or r.name):
            empty += 1
        else:
            kept.append(r)
    log.flush()
    job.dropped_rows = empty + skipped
    job.table.rows = kept
    if job.dropped_rows:
        emit(f"{job.path.name}: dropped {job.dropped_rows} row(s) "
              f"({empty} empty, {skipped} by name)")


def step100_weights(job: Job, cfg) -> None:
    try:
        filled = fill_missing_weights(job.table)
    except ValueError as exc:
        job.weight_warning = str(exc)
        emit(f"{job.path.name}: weights not calculated: {exc}")
        return
    if filled:
        total = len(job.table.rows)
        emit(f"{job.path.name}: computed weights for {filled}/{total} row(s)")


def step110_sort_by_weight(job: Job, cfg) -> None:
    """Sort descending by numeric weight; missing/invalid weights go last.
    Equal weights retain their source order."""
    def sort_key(row: Row) -> float:
        weight = parse_float(row.weight)
        return weight if weight is not None else float("-inf")

    job.table.rows.sort(key=sort_key, reverse=True)


def step120_write(job: Job, cfg) -> None:
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


def step130_check(job: Job, cfg) -> None:
    """Log-only sanity checks on the finished table. Findings are stored on
    the job and printed in the final summary; nothing is changed."""
    rows = job.table.rows
    job.findings = (check_tickers(rows) + check_isins(rows) + check_exchanges(rows, cfg.exchanges_path)
                    + check_weights(rows, job.path.stem))
    if job.weight_warning:
        job.findings.append(f"weight calculation: {job.weight_warning}")


# ---------------------------------------------------------------- helpers --
def _is_excluded(row: Row) -> bool:
    """True when any configured exclusion matches the row."""
    return any((getattr(row, field) or "").strip().upper() == value.strip().upper()
               for field, value in EXCLUDE_RULES.items())


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
    Complete lookup issues are retained separately for the diagnostic CSV."""
    PER_ROW_LIMIT = 10

    def __init__(self, name: str, job: Job | None = None, step: str = ""):
        self.name = name
        self.job = job
        self.step = step
        self.shown = 0
        self.hidden = 0

    def say(self, message: str) -> None:
        if self.shown < self.PER_ROW_LIMIT:
            self.shown += 1
            emit(f"{self.name}: {message}")
        else:
            self.hidden += 1

    def flush(self) -> None:
        if self.hidden:
            emit(f"{self.name}: ... and {self.hidden} more per-row message(s) not shown")


def _lookup_row(log: _RowLog, row: Row, lookup, skipped: str, preferred_ticker=None, **criteria):
    """Shared row logging for missing, unmatched, or ambiguous searches."""
    missing = [field for field, value in criteria.items() if not (value or "").strip()]
    if missing:
        if log.job:
            log.job.lookup_issues.append((log.step, "missing_fields", row, ", ".join(missing)))
        log.say(f"row {_who(row)}: missing {', '.join(missing)} -> {skipped}")
        return None
    try:
        hit = lookup.find(preferred_ticker=preferred_ticker, **criteria)
    except AmbiguousLookupError as exc:
        if log.job:
            log.job.lookup_issues.append((log.step, "ambiguous", row, str(exc)))
        log.say(f"row {_who(row)}: {exc} -> {skipped}")
        return None
    if hit is None:
        if log.job:
            log.job.lookup_issues.append((log.step, "no_match", row, str(criteria)))
        log.say(f"row {_who(row)}: no lookup match for {criteria} -> {skipped}")
    return hit
