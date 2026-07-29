import pandas as pd

def normalize_ticker(t):
    return str(t).strip()

def parse_fee(raw):
    """Handle fee values like 0.15, '0.15', "'0.09%", "1.26%" -> float percent as decimal-free number for comparison."""
    if pd.isna(raw):
        return None
    s = str(raw).strip().lstrip("'")
    s = s.rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None

def load_lookup_fees(lookup_path):
    lookup = pd.read_csv(lookup_path, dtype=str)
    lookup["Ticker"] = lookup["Ticker"].map(normalize_ticker)
    lookup["_fee_parsed"] = lookup["Fee"].map(parse_fee)
    return dict(zip(lookup["Ticker"], lookup["_fee_parsed"]))
