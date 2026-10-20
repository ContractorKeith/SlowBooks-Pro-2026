"""Money as text, in the company's home currency (#125).

`home_currency` drove the accounting but every figure printed as dollars:
the screen's formatCurrency, the PDF filters and a dozen sentences each
wrote "$" themselves. This is the one place an amount becomes text on the
server; utils.js formatCurrency is its twin on the screen, and the two keep
the same symbol table (test_home_currency_display checks).

The rule, both sides:

- an amount in the home currency prints with the currency's symbol:
  "$1,234.50", "-R10.00", "€850.00";
- a currency with no symbol here, or an amount in a currency that is not
  the home one, carries its ISO code: "ZAR 1,234.50", "EUR -10.00"
  (the convention foreign documents already used);
- two decimal places and comma grouping everywhere. Where a decimal comma
  or a space group is wanted is a locale question, left for its own change.

The home currency reaches here through a context variable. The app's
HomeCurrencyContextMiddleware sets it for each API request from the
settings table (a dependency cannot: FastAPI runs one in a copy of the
context, so a value set there never reaches the handler), _render sets it
from the company settings a PDF is given, and anything outside a request
reads USD.
"""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

DEFAULT_HOME = "USD"

_CODE = re.compile(r"^[A-Z]{3}$")

# Narrow symbols, as Intl prints them with currencyDisplay: 'narrowSymbol'.
# Keep in step with CURRENCY_SYMBOLS in app/static/js/utils.js.
SYMBOLS = {
    "AUD": "$",
    "BRL": "R$",
    "CAD": "$",
    "CHF": "CHF",
    "CNY": "¥",
    "CZK": "Kč",
    "DKK": "kr",
    "EUR": "€",
    "GBP": "£",
    "HKD": "$",
    "HUF": "Ft",
    "IDR": "Rp",
    "ILS": "₪",
    "INR": "₹",
    "JPY": "¥",
    "KES": "KSh",
    "KRW": "₩",
    "MXN": "$",
    "MYR": "RM",
    "NGN": "₦",
    "NOK": "kr",
    "NZD": "$",
    "PHP": "₱",
    "PLN": "zł",
    "RUB": "₽",
    "SEK": "kr",
    "SGD": "$",
    "THB": "฿",
    "TRY": "₺",
    "TWD": "$",
    "USD": "$",
    "VND": "₫",
    "ZAR": "R",
}

_home: ContextVar[str] = ContextVar("home_currency", default=DEFAULT_HOME)


def normalize_code(value) -> str:
    """ "zar " -> "ZAR"; anything that is not three letters -> ""."""
    code = (value or "").strip().upper()
    return code if _CODE.fullmatch(code) else ""


def home_currency() -> str:
    """The home currency for the request or render in progress."""
    return _home.get()


@contextmanager
def using_home_currency(code):
    """Make `code` the home currency while the block runs. A blank or
    malformed code keeps USD, as the accounting side does."""
    token = _home.set(normalize_code(code) or DEFAULT_HOME)
    try:
        yield
    finally:
        _home.reset(token)


def read_home_currency(db) -> str:
    """The home currency the settings table holds, or USD. One row; a
    missing table (first run) is dollars, as before."""
    from app.models.settings import Settings

    try:
        value = (
            db.query(Settings.value).filter(Settings.key == "home_currency").scalar()
        )
    except Exception:
        db.rollback()
        return DEFAULT_HOME
    return normalize_code(value) or DEFAULT_HOME


def set_home_currency(code):
    """For the middleware: set and hand back the token to reset with."""
    return _home.set(normalize_code(code) or DEFAULT_HOME)


def reset_home_currency(token) -> None:
    _home.reset(token)


def symbol_for(code: str) -> str | None:
    return SYMBOLS.get(normalize_code(code))


def money_text(value, code=None) -> str:
    """An amount as a sentence or a document shows it. `code` is the
    amount's currency when it is not the home one ("EUR -10.00"); without
    it the amount is in the home currency ("-$10.00", "R1,234.50").
    An amount that rounds to nothing has no sign."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        amount = Decimal(0)
    return money_body(f"{abs(amount):,.2f}", amount < 0, code)


def money_body(body: str, negative: bool, code=None) -> str:
    """Put the sign and currency around an already-formatted magnitude:
    the PDF rate filter formats its own digits (up to four places)."""
    sign = "-" if negative and body.strip("0.,") else ""
    home = home_currency()
    code = normalize_code(code)
    if code and code != home:
        return f"{code} {sign}{body}"
    symbol = SYMBOLS.get(home)
    if symbol is None:
        return f"{home} {sign}{body}"
    return f"{sign}{symbol}{body}"
