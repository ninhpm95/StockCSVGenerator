"""
Reads and overwrites files in INPUT_FOLDER in place (or reports only, in
--dry-run mode). No xlsx->csv conversion; each file keeps its original
format. Only files that actually get trimmed are logged in detail;
everything else is folded into a single "kept as is" count, except
near-misses and missing-sheet cases, which get their own report sections.

For .csv files:
    - Scan column MARKER_COLUMN for any rule in SEARCH_STRS. On the row
      where a rule's search string hits its configured occurrence count,
      delete that row and everything after it.

For .xlsx files:
    - Find the first sheet name in SHEET_NAMES that exists in the
      workbook (other sheets are left alone). Scan that sheet's
      MARKER_COLUMN the same way and delete rows from the match onward.
    - If no matching sheet name exists, the file is left untouched and
      reported separately (not folded into "kept as is").

Requires: pip install openpyxl

Run as a package (`python -m etf_cleaner.run`) or directly
(`python run.py`) - both import styles are supported.
"""

import csv
import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .constants import INPUT_FOLDER, SHEET_NAMES, SEARCH_STRS, MARKER_COLUMN, CutoffRule
except ImportError:  # running as a plain script, not part of a package
    from constants import INPUT_FOLDER, SHEET_NAMES, SEARCH_STRS, MARKER_COLUMN, CutoffRule

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------

@dataclass
class ChangeDetail:
    name: str
    search_str: str
    target_count: int
    row: int  # 1-indexed row number where the cutoff occurred
    sheet: str | None = None


@dataclass
class RunSummary:
    scanned: int = 0
    changed: list[ChangeDetail] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    sheet_missing: list[str] = field(default_factory=list)
    near_misses: list[tuple[str, list[tuple[int, str]]]] = field(default_factory=list)

    @property
    def kept_as_is(self) -> int:
        return self.scanned - len(self.changed) - len(self.failures) - len(self.sheet_missing)


# --------------------------------------------------------------------------
# Normalization / matching
# --------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Strip leading/trailing whitespace, including non-breaking (\\xa0)
    and full-width (\\u3000) spaces that Excel/CSV exports sometimes embed
    but that look identical to a normal space or nothing at all."""
    return s.replace("\xa0", " ").replace("\u3000", " ").strip()


def find_cutoff(rows, rules: list[CutoffRule] = SEARCH_STRS):
    """Scan rows top-down, tracking how many times each rule's search
    string has matched MARKER_COLUMN (after whitespace normalization).
    Return (row_index, search_str, target_count) for the first rule to
    reach its configured occurrence count, or None if none ever does.

    Rules are tracked by list position (not by search string), so two
    rules sharing the same search string but different target counts
    are counted independently rather than colliding.
    """
    counts = [0] * len(rules)
    normalized_targets = [_normalize(rule.search) for rule in rules]
    for i, row in enumerate(rows):
        first_col = _normalize(str(row[MARKER_COLUMN])) if row and row[MARKER_COLUMN] is not None else ""
        for idx, rule in enumerate(rules):
            if first_col == normalized_targets[idx]:
                counts[idx] += 1
                if counts[idx] == rule.occurrence:
                    return i, rule.search, rule.occurrence
    return None


def _near_misses(rows, search_strs: list[str]):
    """Diagnostic: cells that loosely resemble a search string
    (case-insensitive substring, both sides normalized) but didn't
    exact-match after normalization. Surfaces hidden-character/casing
    mismatches instead of failing silently."""
    normalized_search_strs = [_normalize(s) for s in search_strs]
    normalized_targets = set(normalized_search_strs)
    found = []
    for i, row in enumerate(rows):
        raw = str(row[MARKER_COLUMN]) if row and row[MARKER_COLUMN] is not None else ""
        normalized = _normalize(raw)
        if normalized in normalized_targets or not normalized:
            continue
        for s in normalized_search_strs:
            if s.lower() in normalized.lower():
                found.append((i, repr(raw)))
                break
    return found


# --------------------------------------------------------------------------
# CSV handling
# --------------------------------------------------------------------------

def _read_csv_rows(csv_path: Path):
    """Try utf-8 (with or without BOM) first; fall back to cp932
    (Shift-JIS) for Japanese exports that aren't UTF-8. Returns
    (rows, encoding_used), where encoding_used is 'utf-8-sig' only if
    the file actually had a BOM, so writes can preserve that instead of
    always adding one."""
    raw = csv_path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if has_bom else "utf-8")
        return list(csv.reader(io.StringIO(text, newline=""))), ("utf-8-sig" if has_bom else "utf-8")
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("cp932")
        return list(csv.reader(io.StringIO(text, newline=""))), "cp932"
    except UnicodeDecodeError:
        pass
    raise UnicodeDecodeError(
        "unknown", b"", 0, 1, f"Could not decode {csv_path.name} as utf-8(-sig) or cp932"
    )


def _atomic_write_csv(path: Path, rows, encoding: str) -> None:
    """Write to a temp file in the same directory, then atomically
    replace the original. Avoids leaving a half-written/corrupt file
    behind if the process dies mid-write."""
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem)
    try:
        with os.fdopen(fd, "w", newline="", encoding=encoding) as f:
            csv.writer(f).writerows(rows)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def process_csv(csv_path: Path, dry_run: bool = False):
    """Returns (change_detail_or_None, near_misses)."""
    rows, encoding = _read_csv_rows(csv_path)

    match = find_cutoff(rows)
    if match is None:
        return None, _near_misses(rows, [rule.search for rule in SEARCH_STRS])
    cutoff, search_str, target_count = match

    trimmed_rows = rows[:cutoff]
    if not dry_run:
        _atomic_write_csv(csv_path, trimmed_rows, encoding)

    return ChangeDetail(
        name=csv_path.name,
        search_str=search_str,
        target_count=target_count,
        row=cutoff + 1,
    ), []


# --------------------------------------------------------------------------
# XLSX handling
# --------------------------------------------------------------------------

def _unmerge_from_row(ws, start_row: int) -> None:
    """Unmerge any merged cell range that overlaps or lies entirely
    within [start_row, end of sheet]. These ranges are either about to
    be deleted wholesale or straddle the cutoff; either way, leaving
    them merged across a delete_rows() call can corrupt the merge
    metadata (openpyxl doesn't rewrite merged ranges when rows are
    deleted). Ranges entirely above start_row are left untouched."""
    to_unmerge = [
        str(merged_range)
        for merged_range in list(ws.merged_cells.ranges)
        if merged_range.max_row >= start_row
    ]
    for merged_range in to_unmerge:
        ws.unmerge_cells(merged_range)


def _atomic_save_xlsx(wb, path: Path) -> None:
    """Save to a temp file in the same directory, then atomically
    replace the original."""
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp.xlsx", prefix=path.stem)
    os.close(fd)
    try:
        wb.save(tmp_path)
        os.replace(tmp_path, path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def process_xlsx(xlsx_path: Path, dry_run: bool = False):
    """Returns (change_detail_or_None, near_misses, sheet_found: bool)."""
    import openpyxl  # deferred: avoid paying the import cost for CSV-only runs

    wb = openpyxl.load_workbook(xlsx_path)
    try:
        target_sheet = next((name for name in SHEET_NAMES if name in wb.sheetnames), None)
        if target_sheet is None:
            return None, [], False

        ws = wb[target_sheet]
        rows = list(ws.iter_rows(values_only=True))
        match = find_cutoff(rows)

        if match is None:
            return None, _near_misses(rows, [rule.search for rule in SEARCH_STRS]), True
        cutoff, search_str, target_count = match

        if not dry_run:
            # Unmerge any merged ranges touching the rows about to be
            # removed, then delete them in one range call (openpyxl
            # renumbers remaining rows for us; no manual bottom-up
            # iteration needed).
            _unmerge_from_row(ws, cutoff + 1)
            ws.delete_rows(cutoff + 1, ws.max_row - cutoff)
            _atomic_save_xlsx(wb, xlsx_path)

        return ChangeDetail(
            name=xlsx_path.name,
            search_str=search_str,
            target_count=target_count,
            row=cutoff + 1,
            sheet=target_sheet,
        ), [], True
    finally:
        wb.close()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(dry_run: bool = False) -> RunSummary:
    paths = [p for p in sorted(INPUT_FOLDER.iterdir()) if p.suffix.lower() in (".csv", ".xlsx")]
    summary = RunSummary(scanned=len(paths))
    logger.info("Scanning %d file(s) in %s%s", len(paths), INPUT_FOLDER, " (dry run)" if dry_run else "")

    for path in paths:
        try:
            if path.suffix.lower() == ".csv":
                result, misses = process_csv(path, dry_run=dry_run)
                sheet_found = True
            else:
                result, misses, sheet_found = process_xlsx(path, dry_run=dry_run)

            if result is not None:
                summary.changed.append(result)
            elif not sheet_found:
                summary.sheet_missing.append(path.name)
            elif misses:
                summary.near_misses.append((path.name, misses))
        except Exception:
            logger.exception("Failed to process %s", path.name)
            summary.failures.append((path.name, "see log for traceback"))

    logger.info("Num of files kept as is: %d", summary.kept_as_is)

    if summary.changed:
        logger.info("Changed files:")
        for c in summary.changed:
            sheet_note = f" (sheet '{c.sheet}')" if c.sheet else ""
            logger.info('  %s: "%s" (%d) found on row %d%s', c.name, c.search_str, c.target_count, c.row, sheet_note)

    if summary.sheet_missing:
        logger.warning("Files with no matching sheet (left untouched): %s", ", ".join(summary.sheet_missing))

    if summary.failures:
        logger.error("Errored files:")
        for name, err in summary.failures:
            logger.error("  %s: %s", name, err)

    if summary.near_misses:
        logger.warning("Near-misses (cell resembles a search string but didn't exact-match):")
        for name, misses in summary.near_misses:
            for row_index, raw in misses:
                logger.warning("  %s row %d: %s", name, row_index + 1, raw)

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing files.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-level logging.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    run(dry_run=args.dry_run)
