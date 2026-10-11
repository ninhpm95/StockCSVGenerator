"""Conservative instrument classification shared by enrichment and checks."""
import re

_CURRENCIES = {"JPY", "USD", "EUR", "GBP", "HKD", "CNY", "CNH", "KRW",
               "AUD", "CAD", "CHF", "SGD", "SEK", "NOK", "DKK", "NZD"}
_DERIVATIVE_EXCHANGES = {"OSE", "CME", "CBOT", "IOM"}


def instrument_kind(row) -> str:
    code = (row.code or "").strip().lstrip("'").upper()
    name = (row.name or "").upper()
    isin = (row.isin or "").strip().upper()
    exchange = (row.exchange or "").upper()
    security_isin = bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin)) and not isin.startswith("CASH")
    if ((code in _CURRENCIES and not security_isin)
            or any(value.startswith(("CASH", "MARGIN_", "ETD_")) and not security_isin
                   for value in (code, isin)) or name == "CASH"):
        return "cash"
    if any(value.startswith(("FORWARD", "FWRD")) for value in (code, isin)) or re.search(r"\bFORWARD\b", name):
        return "forward"
    if code == "TNOTE" or re.search(r"\b(?:TREASURY BILL|TREASURY NOTE)\b", name):
        return "bond"
    if security_isin:
        return "security"  # e.g. Global X Future Analytics is a listed ETF.
    if re.search(r"\b(?:FUTURES?\b|FUT(?:\b|(?=[A-Z]{3}\d{2})))", name) or (
            exchange in _DERIVATIVE_EXCHANGES and re.fullmatch(r"[A-Z]+[FGHJKMNQUVXZ]\d{1,2}", code)):
        return "future"
    return "security"


def needs_listing_lookup(row) -> bool:
    return instrument_kind(row) not in {"cash", "forward", "future"}
