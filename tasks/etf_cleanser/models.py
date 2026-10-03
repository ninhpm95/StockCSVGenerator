"""Data models shared across the pipeline (raw grid, column map, holding rows,
table, per-file Job) plus number parsing and weight arithmetic (step 050 fills
missing weights; step 090 parses/formats them)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path


# ----------------------------------------------------------------- models --
@dataclass
class Grid:
    """One raw CSV / holdings sheet as read from disk: a grid of string cells."""
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ColumnMap:
    """field name -> column index, for whichever columns were resolved."""
    code: int | None = None
    name: int | None = None
    isin: int | None = None
    shares: int | None = None
    price: int | None = None
    valuation: int | None = None
    weight: int | None = None
    exchange: int | None = None
    currency: int | None = None

    def resolved(self) -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name) is not None]

    def items(self):
        """(field_name, column_index) for every mapped column."""
        return [(f.name, getattr(self, f.name)) for f in fields(self)
                if getattr(self, f.name) is not None]


@dataclass
class Row:
    code: str | None = None
    name: str | None = None
    isin: str | None = None
    shares: str | None = None
    price: str | None = None
    valuation: str | None = None
    weight: str | None = None
    exchange: str | None = None
    currency: str | None = None


def row_from_cells(cells: list[str], colmap: ColumnMap) -> Row:
    row = Row()
    for field_name, idx in colmap.items():
        if idx < len(cells):
            value = cells[idx].strip()
            if value:
                setattr(row, field_name, value)
    return row


@dataclass
class Table:
    """The parsed holdings of one file."""
    rows: list[Row] = field(default_factory=list)


@dataclass
class Job:
    """Everything known about one input file while it travels the pipeline."""
    path: Path
    grid: Grid | None = None       # raw cells: set by step 010, trimmed by 020 and 030
    table: Table | None = None     # parsed rows: set by step 040, used by 050 onward
    error: str | None = None
    findings: list[str] = field(default_factory=list)   # step 100 output, shown in the summary
    dropped_rows: int = 0                               # step 082: rows removed (empty or skippable)


# ----------------------------------------------------------------- values --
_STRIP = re.compile(r"[,\s\u00a0]")


def parse_float(value) -> float | None:
    """'1,234.5', '6.84%', '(0.16)' -> float; anything else (including
    placeholders like '-', 'nan', 'inf') -> None."""
    if value is None:
        return None
    s = _STRIP.sub("", str(value).strip())
    if not s or set(s) <= {"-", "%"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if s.endswith("%"):
        s = s[:-1]
    try:
        number = float(s)
    except ValueError:
        return None
    if not math.isfinite(number):        # 'nan' / 'inf' parse as floats but are placeholders
        return None
    return -number if negative else number


def compute_row_value(row: Row) -> float | None:
    """Best guess of a row's value: shares * price, falling back to valuation."""
    shares, price = parse_float(row.shares), parse_float(row.price)
    if shares is not None and price is not None:
        return shares * price
    return parse_float(row.valuation)


def format_weight(pct: float) -> str:
    """88.25 -> '88.25', 8.074317333 -> '8.074317'."""
    return f"{pct:.6f}".rstrip("0").rstrip(".")


def fill_missing_weights(table: Table) -> int:
    """weight = value / sum(values) * 100 for every row without a usable
    weight. All rows take part (in the denominator and in the fill); there is
    no ticker/ISIN filter. Rows with no computable value stay empty.
    Returns the number of rows filled."""
    rows = table.rows
    if not any(parse_float(r.weight) is None for r in rows):
        return 0
    values = [compute_row_value(r) for r in rows]
    total = sum(v for v in values if v is not None)
    if not total:
        return 0
    filled = 0
    for row, value in zip(rows, values):
        if value is None or parse_float(row.weight) is not None:
            continue
        row.weight = format_weight(value / total * 100.0)
        filled += 1
    return filled
