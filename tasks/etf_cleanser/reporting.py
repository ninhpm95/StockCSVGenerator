"""Console progress, project logging, and complete lookup diagnostics."""
import builtins
import csv
import logging
from collections import Counter

from .instruments import needs_listing_lookup


def emit(message=""):
    builtins.print(message)
    logging.getLogger("tasks.etf_cleanser").info(message)


def write_diagnostics(jobs, output_dir):
    report_dir = output_dir / "diagnostics"
    report_dir.mkdir()
    counts = Counter()
    with (report_dir / "lookup_issues.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["File", "Output line", "Step", "Reason", "Ticker", "Name", "ISIN", "Exchange", "Weight(%)", "Details"])
        for job in jobs:
            positions = {id(row): i + 2 for i, row in enumerate(job.table.rows)} if job.table else {}
            for step, reason, row, details in job.lookup_issues:
                counts[reason] += 1
                writer.writerow([job.path.name, positions.get(id(row), ""), step, reason,
                                 row.code, row.name, row.isin, row.exchange, row.weight, details])
    with (report_dir / "identifier_coverage.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["File", "Exchange", "Eligible rows", "With ticker", "With ISIN"])
        for job in jobs:
            if job.error or not job.table:
                continue
            groups = {}
            for row in job.table.rows:
                if needs_listing_lookup(row):
                    groups.setdefault(row.exchange or "unknown", []).append(row)
            for exchange, rows in sorted(groups.items()):
                writer.writerow([job.path.name, exchange, len(rows), sum(bool(r.code) for r in rows),
                                 sum(bool(r.isin) for r in rows)])
    emit("Lookup issues: " + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"))
