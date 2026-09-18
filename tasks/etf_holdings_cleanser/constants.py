"""Configuration for the ETF file-cleansing script."""

from dataclasses import dataclass
from pathlib import Path

# Directory containing this file (i.e. the package dir), not the repo root.
PACKAGE_DIR = Path(__file__).resolve().parent

# Directory two levels above the package dir. Comment/assumption: this
# package lives at <repo_root>/<some_dir>/etf_cleaner, so two `.parent`
# hops lands on <repo_root>. If this package is ever moved to a
# different depth (e.g. flattened to <repo_root>/etf_cleaner), this will
# silently point somewhere else -- there is no reliable way to detect that
# generically without a repo-root marker (.git, pyproject.toml, etc.), so
# if INPUT_FOLDER ever looks wrong, check this line first rather than
# assuming it's correct.
REPO_ROOT = PACKAGE_DIR.parent.parent
INPUT_FOLDER = REPO_ROOT / "data" / "ETFs"

SHEET_NAMES = ["保有明細"]

# 0-indexed column that holds the marker strings we scan for.
MARKER_COLUMN = 0


@dataclass(frozen=True)
class CutoffRule:
    """On the `occurrence`-th time `search` appears in MARKER_COLUMN
    (scanning rows top-down), delete that row and everything after it."""
    search: str
    occurrence: int

    def __post_init__(self):
        if self.occurrence <= 0:
            raise ValueError(
                f"CutoffRule.occurrence must be >= 1, got {self.occurrence!r} "
                f"for search={self.search!r} (an occurrence of 0 or less can never match)."
            )


# Rules are checked in row order first: whichever rule reaches its target
# occurrence count on the earliest row wins. List order (below) only breaks
# ties between rules that would both hit their target on the very same row.
CUTOFF_RULES = [
    CutoffRule("Fund Holdings as of", 2),
]
