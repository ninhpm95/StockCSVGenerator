import os
import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from .constants import TV_SLEEP_LOOKUP
from .fields import FAST

EXCHANGES_PATH = Path(__file__).resolve().parents[2] / 'data' / 'exchanges.csv'


@lru_cache(maxsize=1)
def load_exchanges() -> tuple[dict, ...]:
    with EXCHANGES_PATH.open(newline='', encoding='utf-8-sig') as source:
        reader = csv.DictReader(source)
        required = {'exchange', 'short_name', 'lookup_name', 'country',
                    'yahoo_suffix', 'yahoo_exchange_codes', 'yahoo_dot_replacement',
                    'tv_screener', 'tv_exchange'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{EXCHANGES_PATH}: missing columns: {", ".join(sorted(missing))}')
        return tuple({key: (value or '').strip() for key, value in row.items()}
                     for row in reader)


def _exchange_rows(exchange: str | None) -> list[dict]:
    key = (exchange or '').strip().casefold()
    if not key:
        return []
    rows = load_exchanges()
    matches = [row for row in rows if key in {
        row['exchange'].casefold(), row['short_name'].casefold(),
        *(code.strip().casefold() for code in row['yahoo_exchange_codes'].split('|') if code.strip()),
    }]
    return matches or [row for row in rows if key == row['lookup_name'].casefold()]


def _unique_value(rows: list[dict], field: str) -> str | None:
    values = {row[field] for row in rows}
    return next(iter(values)) if len(values) == 1 else None


def _market_value(region: str, exchange: str | None, field: str) -> str:
    rows = _exchange_rows(exchange)
    if not rows:
        rows = [row for row in load_exchanges() if row['country'] == region.upper()
                and row['yahoo_suffix'] != 'N/A']
    value = _unique_value(rows, field)
    if value is None or value == 'N/A':
        raise ValueError(f'No unambiguous {field} configured for exchange={exchange!r}, country={region!r}')
    return value


def get_region(filename: str, exchange: str | None = None) -> str:
    country = _unique_value(_exchange_rows(exchange), 'country')
    if country:
        return country
    base_name = os.path.basename(filename).upper()
    countries = list(dict.fromkeys(row['country'] for row in load_exchanges() if row['country']))
    tokens = re.split(r'[^A-Z]+', os.path.splitext(base_name)[0])
    matches = [country for country in countries if country in tokens]
    if not matches:
        matches = [country for country in countries if country in base_name]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f'Could not determine region from exchange {exchange!r} or file name {filename!r}')

def get_tv_sleep_range(ticker_num: int, speed: str) -> Optional[tuple[int, int]]:
    if speed == FAST:
        return (1, 2)
    for limit, sleep_range in TV_SLEEP_LOOKUP:
        if ticker_num < limit:
            return sleep_range
    return None  # unreachable while TV_SLEEP_LOOKUP ends in a float("inf") sentinel

def prepare_ticker(ticker: str) -> str:
    ticker = str(ticker).strip().lstrip("'")
    return ticker

def format_ticker_for_yfinance(ticker: str, region: str, exchange: str | None = None) -> str:
    ticker = prepare_ticker(ticker)
    suffix = _market_value(region, exchange, 'yahoo_suffix')
    if suffix and ticker.upper().endswith(suffix.upper()):
        ticker = ticker[:-len(suffix)]
    replacement = _market_value(region, exchange, 'yahoo_dot_replacement')
    if replacement:
        ticker = ticker.replace('.', replacement)
    return ticker + suffix

def get_tv_screener(region: str, exchange: str | None = None) -> str:
    return _market_value(region, exchange, 'tv_screener')

def map_exchange(exchange: str | None) -> Optional[str]:
    return _unique_value(_exchange_rows(exchange), 'tv_exchange') or None
