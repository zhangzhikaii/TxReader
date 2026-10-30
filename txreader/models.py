"""Data shared by all screenshot parsers."""
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Tx:
    month: int
    day: int
    amount: float      # negative = expense, positive = income
    merchant: str
    year: int | None = None    # None when the screenshot showed no year
    hour: int = 0
    minute: int = 0
    second: int = 0            # WeChat lists only show minutes
    balance: float | None = None  # account balance after this transaction, if shown
    sign_guessed: bool = False    # +/- sign inferred rather than read


@dataclass
class Resolved:
    dt: datetime
    year_guessed: bool


def resolve_date(tx: Tx, default_year: int, today: date | None = None) -> Resolved:
    """Fill in a missing year: use default_year, stepping back a year if that
    would put the transaction in the future."""
    time = (tx.hour, tx.minute, tx.second)
    if tx.year:
        return Resolved(datetime(tx.year, tx.month, tx.day, *time), False)
    today = today or date.today()
    y = default_year
    if y == today.year and (tx.month, tx.day) > (today.month, today.day):
        y -= 1
    return Resolved(datetime(y, tx.month, tx.day, *time), True)
