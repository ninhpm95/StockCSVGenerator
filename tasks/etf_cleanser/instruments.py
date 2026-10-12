"""Conservative instrument classification shared by enrichment and checks."""
import re

_CURRENCIES = {"JPY", "USD", "EUR", "GBP", "HKD", "CNY", "CNH", "KRW",
               "AUD", "CAD", "CHF", "SGD", "SEK", "NOK", "DKK", "NZD",
               "TWD", "MXN", "BRL", "ZAR", "PLN", "MYR", "AED", "CZK",
               "TRY", "HUF", "INR", "SAR", "IDR", "THB", "PHP", "CLP",
               "QAR", "KWD", "RUB", "COP", "EGP", "ILS"}
_DERIVATIVE_EXCHANGES = {"OSE", "CME", "CBOT", "IOM"}
# Compact Next Funds contract labels observed in holdings: root + YYMM.
# Restrict both the root and expiry rather than guessing from any trailing digits.
_FUTURE_ROOTS = ("CAN60", "CHA50", "DAXMN", "DJREA", "DJIAVMN", "EURO50",
                 "FT100", "GOLD", "HWB", "IFSNIFTY", "KLCI", "MSCIBRIX",
                 "MSCICHA", "MSCIN", "NASDAQMN", "SET50", "SPEMINI", "SPI200",
                 "STX600RE", "SWISSMKT", "JPXNIKKEI400 F", "TSEREIT")
_COMPACT_FUTURE = re.compile(
    r"(?:" + "|".join(_FUTURE_ROOTS) + r")\s*\d{2}(?:0[1-9]|1[0-2])")
# Global X labels use month names; match observed contract roots, not company names.
_MONTH_EXPIRY = r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}"
_DATED_FUTURE = re.compile(
    r"(?:NYSE FANG\+ IDX FU |DJ US REAL ESTATE |DJIA MICR MIN CBOT|"
    r"PHLX EMINI SEMICO |NASD100 MICRO EMIN|NASDAQ 100 E-MINI |E-MINI RUSS 2000 )"
    + _MONTH_EXPIRY)
_FX_FUTURE = re.compile(r"([A-Z]{3})/([A-Z]{3}) " + _MONTH_EXPIRY)
_FUTURE_EXPIRY = re.compile(
    r"(?:\d{2}(?:0[1-9]|1[0-2])|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"[ .]?(?:\d{2}|\d{4}))\)?$")
# Next Funds bond identifiers, not stock exchange tickers. Require an ISIN too.
_PROVIDER_BOND = re.compile(r"B\.(?:\d{4}\.\d+(?:\.\d+)?|\d{8}\.(?:SB|GB|MO)\d+)")


def instrument_kind(row) -> str:
    code = (row.code or "").strip().lstrip("'").upper()
    name = (row.name or "").strip().upper()
    isin = (row.isin or "").strip().upper()
    exchange = (row.exchange or "").upper()
    security_isin = bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin)) and not isin.startswith("CASH")
    # Currency codes overlap stock tickers (COP = Colombian peso / ConocoPhillips).
    # Require a currency label or FX-pair name; a bare code is never enough.
    currency_label = code in _CURRENCIES and (
        name == code or (re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", name)
                         and all(part in _CURRENCIES for part in name.split("/"))))
    if ((currency_label and not security_isin)
            or any(value.startswith(("CASH", "MARGIN_", "ETD_")) and not security_isin
                   for value in (code, isin)) or name == "CASH"
            or (not security_isin and (
                name.startswith("CASH COLLATERAL ")
                or re.fullmatch(r"[A-Z]{3} CASH(?:\(COMMITTED\))?", name)))):
        return "cash"
    # A company name containing FORWARD (e.g. MONEY FORWARD) is not a contract.
    forward_identifier = r"(?:FORWARD|(?:FORWARD|FWRD)[A-Z]{6}\d{2})"
    if not security_isin and any(re.fullmatch(forward_identifier, value)
                                 for value in (code, isin, name)):
        return "forward"
    if (code in {"TNOTE", "TBOND"}
            or re.search(r"\bTREASURY (?:BILL|NOTE|BOND)\b", name)
            or (code in {"JTDB", "JPGV"} and name.startswith("JAPAN (GOVERNMENT OF) #"))
            or (security_isin and _PROVIDER_BOND.fullmatch(code))):
        return "bond"
    if security_isin:
        return "security"  # e.g. Global X Future Analytics is a listed ETF.
    fx_future = _FX_FUTURE.fullmatch(name)
    if (_COMPACT_FUTURE.fullmatch(name) or _COMPACT_FUTURE.fullmatch(code)
            or _DATED_FUTURE.fullmatch(name)
            or (exchange == "NGC" and fx_future
                and all(currency in _CURRENCIES for currency in fx_future.groups()))
            or (re.search(r"\b(?:FUTURES?\b|FUT(?:\b|(?=[A-Z]{3}\d{2})))", name)
                and _FUTURE_EXPIRY.search(name)) or (
            exchange in _DERIVATIVE_EXCHANGES and re.fullmatch(r"[A-Z]+[FGHJKMNQUVXZ]\d{1,2}", code))):
        return "future"
    return "security"


def needs_listing_lookup(row) -> bool:
    """Equity listing lookups do not apply to cash, derivatives, or bonds."""
    return instrument_kind(row) == "security"
