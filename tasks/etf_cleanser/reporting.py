"""Detailed project logging and complete lookup diagnostics."""
import logging
from collections import Counter

from .instruments import needs_listing_lookup


def emit(message="", level=logging.INFO):
    """Send details to the handlers configured by main, never the terminal."""
    logging.getLogger("tasks.etf_cleanser").log(level, message)


def detail(message):
    """Full detail stays in the same file, separate from INFO/WARNING summaries."""
    emit(message, level=logging.DEBUG)


def _display(value):
    """One missing-value spelling and one physical line per log record."""
    if value is None or value == "":
        return "missing"
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _issue_resolved(step, row):
    """An earlier missing-field lookup can be satisfied by a later fallback."""
    return bool(row.exchange) if step == "exchange" else bool(row.isin) if step == "isin" else False


def log_diagnostics(jobs):
    """Keep complete lookup events and final identifier coverage in the run log."""
    counts = Counter()
    resolved = 0
    for job in jobs:
        counts.update(reason for _, reason, _, _ in job.lookup_issues)
        resolved += sum(_issue_resolved(step, row) for step, _, row, _ in job.lookup_issues)
    if counts:
        detail("LOOKUP DETAILS | counts are attempts; identifiers shown after enrichment")
        for job in jobs:
            positions = {id(row): i + 2 for i, row in enumerate(job.table.rows)} if job.table else {}
            groups = {}
            for issue in job.lookup_issues:
                groups.setdefault(issue[0], []).append(issue)
            for step, issues in groups.items():
                detail(f"LOOKUP FILE | file={_display(job.path.name)} | output={_display(job.output_filename)}"
                       f" | step={step} | attempts={len(issues)}")
                for _, reason, row, search in issues:
                    status = "resolved_later" if _issue_resolved(step, row) else "unresolved"
                    detail(f"  output_line={positions.get(id(row), 'missing')} | status={status} | reason={reason}"
                           f" | ticker={_display(row.code)} | isin={_display(row.isin)}"
                           f" | exchange={_display(row.exchange)} | weight={_display(row.weight)}"
                           f" | name={_display(row.name)} | search={_display(search)}")

    detail("COVERAGE DETAILS | listing-eligible rows; final output state")
    totals = Counter()
    gaps = []
    for job in jobs:
        if job.error or not job.table:
            continue
        groups = {}
        for row in job.table.rows:
            if needs_listing_lookup(row):
                groups.setdefault(row.exchange or "missing", []).append(row)
        file_totals = Counter()
        for exchange, rows in sorted(groups.items()):
            tickers = sum(bool(r.code) for r in rows)
            isins = sum(bool(r.isin) for r in rows)
            file_totals.update(eligible=len(rows), missing_ticker=len(rows) - tickers,
                               missing_isin=len(rows) - isins,
                               missing_exchange=sum(not r.exchange for r in rows))
            detail(f"COVERAGE | file={_display(job.path.name)} | output={_display(job.output_filename)} | exchange={exchange}"
                   f" | eligible={len(rows)} | with_ticker={tickers} | with_isin={isins}")
        totals.update(file_totals)
        missing = sum(file_totals[key] for key in ("missing_ticker", "missing_isin", "missing_exchange"))
        if missing:
            gaps.append((missing, job.path.name, file_totals))

    # Keep the useful overview together at the bottom, after full DEBUG records.
    emit("LOOKUPS | " + (" | ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none")
         + f" | attempts={sum(counts.values())} | resolved_later={resolved} | unresolved={sum(counts.values()) - resolved}")
    emit(f"IDENTIFIERS | eligible={totals['eligible']} | missing_ticker={totals['missing_ticker']}"
         f" | missing_isin={totals['missing_isin']} | missing_exchange={totals['missing_exchange']}"
         f" | files_with_gaps={len(gaps)}",
         level=logging.WARNING if gaps else logging.INFO)
    if gaps:
        emit("COVERAGE GAPS | top 10 files by missing fields (a row may lack multiple fields)")
        for _, filename, coverage in sorted(gaps, key=lambda gap: (-gap[0], gap[1]))[:10]:
            emit(f"  file={_display(filename)} | eligible={coverage['eligible']}"
                 f" | missing_ticker={coverage['missing_ticker']} | missing_isin={coverage['missing_isin']}"
                 f" | missing_exchange={coverage['missing_exchange']}")
