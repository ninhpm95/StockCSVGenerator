"""The pipeline steps. Each is stepNNN_name(job, cfg): it mutates job in
place and does its own bookkeeping (looping rows, counting, printing). The
reusable logic lives in the sibling modules - parsing.py, models.py,
lookup.py, checks.py - so the steps stay thin. run.py runs them
in order via run_step; nothing else calls them."""
from __future__ import annotations

import csv
import logging
import re

from .checks import check_exchanges, check_isins, check_tickers, check_weights
from .config import (OUTPUT_COLUMNS, SKIP_NAME_TEXTS, CODE_TO_CHECK_NAME_TO_SKIP,
                     TICKER_FILENAME_PATTERN, EXCLUDE_RULES, FILENAME_COUNTRY_EXCHANGES)
from .lookup import AmbiguousLookupError, get_lookup, get_exchange_names
from .manual_updates import apply_manual_updates
from .instruments import needs_listing_lookup
from .reporting import emit, detail
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
        detail(f"{job.path.name}: last 'Fund Holdings as of' at row {idx + 1} "
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
        detail(f"{job.path.name}: header at row {header_idx + 1} "
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
    detail(f"{job.path.name}: resolved columns [{', '.join(resolved)}], {len(rows)} data row(s)")


def step041_manual_updates(job: Job, cfg) -> None:
    """Apply explicit manual corrections immediately after columns are resolved."""
    changed = apply_manual_updates(job.table.rows, cfg.manual_update_rules)
    if changed:
        detail(f"{job.path.name}: manual updates changed {changed} row(s)")


def step050_exchange(job: Job, cfg) -> None:
    """Every file: fill the Exchange of rows that have none from the lookup
    by Ticker + ISIN, then use an explicitly mapped filename country as
    a last resort. Existing exchanges and non-listing instruments are retained."""
    name = job.path.name
    lookup = get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    log = _RowLog(name, job, "exchange")
    country_match = re.match(rf"{TICKER_FILENAME_PATTERN}_([A-Z]{{2}})_", job.path.stem, re.IGNORECASE)
    fallback_exchange = (FILENAME_COUNTRY_EXCHANGES.get(country_match[1].upper())
                         if country_match else None)
    filled = filename_filled = not_found = no_exchange = 0
    for row in job.table.rows:
        if row.exchange or not needs_listing_lookup(row):
            continue
        hit = _lookup_row(log, row, lookup, "Exchange not found in lookup",
                          ticker=row.code, isin=row.isin)
        if hit and hit.exchange:
            row.exchange = hit.exchange
            filled += 1
        elif fallback_exchange:
            row.exchange = fallback_exchange
            filename_filled += 1
        elif hit is None:
            not_found += 1
        else:
            job.lookup_issues.append(("exchange", "lookup_missing_value", row, "exchange"))
            log.say(f"row {_who(row)}: lookup entry has no exchange -> Exchange not filled")
            no_exchange += 1
    log.flush()

    if filled or filename_filled or not_found or no_exchange:
        detail(f"{name}: exchange lookup_filled={filled}, filename_filled={filename_filled}"
               + (f" ({country_match[1].upper()} -> {fallback_exchange})" if filename_filled else "")
               + f", unresolved={not_found}, lookup_missing_exchange={no_exchange}")


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

    if converted:
        detail(f"{job.path.name}: {converted} Exchange value(s) unified to lookup name")


def step070_refresh_from_isin(job: Job, cfg) -> None:
    """Correct tickers before ISIN backfill; default scope is ticker-named CSVs.
    cfg.refresh_tickers_all_files expands the scope to all supported files.
    Require existing ISIN + Exchange to match uniquely; leave unresolved
    rows untouched. Names, ISINs and exchanges are retained."""
    name = job.path.name
    if (not cfg.refresh_tickers_all_files
            and (job.path.suffix.lower() != ".csv"
                 or not re.fullmatch(TICKER_FILENAME_PATTERN, job.path.stem))):
        return
    lookup = get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    log = _RowLog(name, job, "ticker")
    refreshed = unchanged = not_found = no_ticker = excluded = 0
    for row in job.table.rows:
        if _is_excluded(row) or not needs_listing_lookup(row):
            excluded += 1
            continue
        # Prefer an existing ticker within ambiguous ISIN/exchange candidates.
        # If the source code is not a counter ticker (e.g. SEDOL), the holding's
        # currency can resolve a standard HKEX HKD/RMB equity pair. See lookup.py.
        hit = _lookup_row(log, row, lookup, "not refreshed",
                          preferred_ticker=row.code, preferred_currency=row.currency,
                          isin=row.isin, exchange=row.exchange)
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
    if refreshed or not_found or no_ticker:
        detail(f"{name}: ticker refreshed={refreshed}, unchanged={unchanged}, "
               f"unresolved={not_found}, lookup_missing_ticker={no_ticker}, excluded={excluded}")


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
    if filled or unmatched or no_code:
        detail(f"{job.path.name}: ISIN filled={filled}, unresolved={unmatched}, missing_ticker={no_code}")


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
        detail(f"{job.path.name}: dropped {job.dropped_rows} row(s) "
              f"({empty} empty, {skipped} by name)")


def step100_weights(job: Job, cfg) -> None:
    try:
        filled = fill_missing_weights(job.table)
    except ValueError as exc:
        job.weight_warning = str(exc)
        emit(f"WEIGHTS | file={job.path.name} | calculation skipped: {exc}", level=logging.WARNING)
        return
    if filled:
        total = len(job.table.rows)
        detail(f"{job.path.name}: computed weights for {filled}/{total} row(s)")


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
    """Cleanup samples are capped; lookup events are logged once at run end."""
    PER_ROW_LIMIT = 10

    def __init__(self, name: str, job: Job | None = None, step: str = ""):
        self.name = name
        self.job = job
        self.step = step
        self.shown = 0
        self.hidden = 0

    def say(self, message: str) -> None:
        if self.job is not None:
            # Lookup issues already contain this information, without truncation.
            return
        if self.shown < self.PER_ROW_LIMIT:
            self.shown += 1
            detail(f"{self.name}: {message}")
        else:
            self.hidden += 1

    def flush(self) -> None:
        if self.hidden:
            detail(f"{self.name}: ... and {self.hidden} more cleanup message(s) not shown")


def _lookup_row(log: _RowLog, row: Row, lookup, skipped: str, preferred_ticker=None,
                preferred_currency=None, **criteria):
    """Shared row logging for missing, unmatched, or ambiguous searches."""
    missing = [field for field, value in criteria.items() if not (value or "").strip()]
    if missing:
        if log.job:
            log.job.lookup_issues.append((log.step, "missing_fields", row, ", ".join(missing)))
        log.say(f"row {_who(row)}: missing {', '.join(missing)} -> {skipped}")
        return None
    try:
        hit = lookup.find(preferred_ticker=preferred_ticker,
                          preferred_currency=preferred_currency, **criteria)
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
