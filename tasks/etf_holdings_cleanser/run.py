"""
Reads and overwrites files in INPUT_FOLDER in place. No xlsx->csv
conversion; each file keeps its original format. Only files that
actually get trimmed are logged.

For .csv files:
    - Scan column A for any string in SEARCH_STRS. On the row where a
      string hits its configured occurrence count, delete that row and
      everything after it.

For .xlsx files:
    - Find the first sheet name in SHEET_NAMES that exists in the
      workbook (other sheets are left alone). Scan that sheet's column A
      the same way and delete rows from the match onward.
    - If no matching sheet name exists, the file is left untouched.

Requires: pip install openpyxl
"""

import csv
from pathlib import Path
import openpyxl
from .constants import INPUT_FOLDER, SHEET_NAMES, SEARCH_STRS


def find_cutoff(rows):
    """Scan rows top-down, tracking how many times each search string has
    matched column A. Return the row index at which the first search
    string reaches its configured occurrence count, or None."""
    counts = {search_str: 0 for search_str, _ in SEARCH_STRS}
    for i, row in enumerate(rows):
        first_col = str(row[0]) if row and row[0] is not None else ""
        for search_str, target_count in SEARCH_STRS:
            if search_str.lower() in first_col.lower():
                counts[search_str] += 1
                if counts[search_str] == target_count:
                    return i
    return None


def process_csv(csv_path: Path):
    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    cutoff = find_cutoff(rows)
    if cutoff is None:
        return

    rows = rows[:cutoff]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    print(f"  [trim] {csv_path.name}: removed from row {cutoff + 1} onward")


def process_xlsx(xlsx_path: Path):
    wb = openpyxl.load_workbook(xlsx_path)

    target_sheet = None
    for name in SHEET_NAMES:
        if name in wb.sheetnames:
            target_sheet = name
            break

    if target_sheet is None:
        return

    ws = wb[target_sheet]
    rows = list(ws.iter_rows(values_only=True))
    cutoff = find_cutoff(rows)

    if cutoff is None:
        return

    # delete bottom-up so row numbers don't shift mid-delete
    ws.delete_rows(cutoff + 1, ws.max_row - cutoff)
    wb.save(xlsx_path)
    print(f"  [trim] {xlsx_path.name}: removed from row {cutoff + 1} onward (sheet '{target_sheet}')")


def run():
    for path in sorted(INPUT_FOLDER.iterdir()):
        if path.suffix.lower() == ".csv":
            process_csv(path)
        elif path.suffix.lower() == ".xlsx":
            process_xlsx(path)
