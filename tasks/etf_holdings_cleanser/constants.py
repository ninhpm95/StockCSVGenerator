from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_FOLDER = PROJECT_ROOT.parent.parent / "data" / "ETFs"
OUTPUT_FOLDER = PROJECT_ROOT / "cleansed_ETFs"

SHEET_NAMES = ["保有明細"]

# Each tuple is (search string, occurrence count to cut at).
# e.g. ("Fund Holdings as of", 2) -> on the 2nd match, delete that row
# and everything after it. First match in SEARCH_STRS order (scanning
# top-down) to hit its target count wins.
SEARCH_STRS = [
    ("Fund Holdings as of", 2),
    ("Test", 1),
]
