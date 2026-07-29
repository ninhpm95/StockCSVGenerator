import pandas as pd

def normalize_ticker(t):
    return str(t).strip()

def parse_number(raw):
    """Convert values like 0.15, '0.15', "'0.09%", "1.26%" to float or None if invalid."""
    if pd.isna(raw):
        return None
    s = str(raw).strip().lstrip("'")
    has_percent = s.endswith("%")
    s = s.rstrip("%")
    try:
        value = float(s)
    except ValueError:
        return None
    return value / 100 if has_percent else value

def load_lookup_fees(lookup_path):
    lookup = pd.read_csv(lookup_path, dtype=str)
    lookup["Ticker"] = lookup["Ticker"].map(normalize_ticker)
    lookup["Fee"] = lookup["Fee"].map(parse_number)
    return dict(zip(lookup["Ticker"], lookup["Fee"]))
