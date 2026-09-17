"""
fields.py -- canonical field-name literals shared by constants.py and run.py.

Centralizing these avoids typo-prone raw string keys scattered across
FIELD_CANDIDATES, NORMALIZERS, the holding/resolved-record dicts, and the
output CSV columns. Kept as a plain class (not an Enum) since these values
are used directly as dict keys and DataFrame column names -- an Enum would
require unwrapping `.value` everywhere it's used that way.
"""


class Fields:
    TICKER = "Ticker"
    NAME = "Name"
    ISIN = "ISIN"
    EXCHANGE = "Exchange"
    CURRENCY = "Currency"
    REGION = "Region"
    LOCATION = "Location"
    WEIGHT = "Weight"
    PRICE = "Price"
    SHARES = "Shares"
