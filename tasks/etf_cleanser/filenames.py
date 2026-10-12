"""Load ETF metadata for output filenames without changing ticker strings."""
import csv
import re
from functools import lru_cache


@lru_cache(maxsize=None)
def get_etf_filenames(path: str) -> dict[str, str]:
    filenames = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns = ("Ticker", "Region", "Company", "ShortName")
        if not set(columns).issubset(reader.fieldnames or []):
            raise ValueError(f"ETF list must contain columns: {', '.join(columns)}")
        for line, row in enumerate(reader, start=2):
            parts = [(row.get(column) or "").strip() for column in columns]
            if not any(parts):
                continue
            if any(not part or re.search(r'[<>:"/\\|?*\x00-\x1f]', part)
                   or part.endswith((".", " ")) for part in parts):
                raise ValueError(f"ETF list row {line} has missing or invalid filename fields")
            ticker = parts[0].upper()
            filename = "_".join(parts) + ".csv"
            if ticker in filenames and filenames[ticker] != filename:
                raise ValueError(f"ETF list has conflicting filename metadata for ticker {ticker}")
            filenames[ticker] = filename
    return filenames
