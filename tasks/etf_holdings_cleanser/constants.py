"""Configuration for the ETF file-cleansing script."""

from dataclasses import dataclass
from pathlib import Path

# Directory containing this file (i.e. the package dir), not the repo root.
PACKAGE_DIR = Path(__file__).resolve().parent

# Two levels above the package dir -> <repo_root>/data/ETFs.
INPUT_FOLDER = PACKAGE_DIR.parent.parent / "data" / "ETFs"

SHEET_NAMES = ["保有明細"]

# 0-indexed column that holds the marker strings we scan for.
MARKER_COLUMN = 0


@dataclass(frozen=True)
class CutoffRule:
    """On the `occurrence`-th time `search` appears in MARKER_COLUMN
    (scanning rows top-down), delete that row and everything after it."""
    search: str
    occurrence: int


# Rules are checked in row order first: whichever rule reaches its target
# occurrence count on the earliest row wins. List order (below) only breaks
# ties between rules that would both hit their target on the very same row.
SEARCH_STRS = [
    CutoffRule("Fund Holdings as of", 2),
]
