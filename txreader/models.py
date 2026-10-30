"""Data shared by all screenshot parsers."""
import calendar
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Tx:
    month: int | None  # None when no month header was visible (then `weekday` is needed)
    day: int
    amount: float      # negative = expense, positive = income
    merchant: str
    year: int | None = None    # None when the screenshot showed no year
    hour: int = 0
    minute: int = 0
    second: int = 0            # WeChat lists only show minutes
    weekday: int | None = None    # 0 = Monday, as shown on screen
    balance: float | None = None  # account balance after this transaction, if shown
    sign_guessed: bool = False    # +/- sign inferred rather than read


@dataclass
class Resolved:
    dt: datetime
    date_guessed: bool  # year or month was inferred, not read from the screenshot


def resolve_date(tx: Tx, default_year: int, today: date | None = None) -> Resolved:
    """Fill in a missing year or month.

    Missing year: use default_year, stepping back a year if that lands in the future.
    Missing month: the latest date up to today whose day and weekday match the screen.
    """
    today = today or date.today()
    time = (tx.hour, tx.minute, tx.second)

    if tx.month is not None:
        if tx.year:
            return Resolved(datetime(tx.year, tx.month, tx.day, *time), False)
        y = default_year
        if y == today.year and (tx.month, tx.day) > (today.month, today.day):
            y -= 1
        return Resolved(datetime(y, tx.month, tx.day, *time), True)

    if tx.weekday is None:
        raise ValueError("看不到月份，也看不到星期，无法确定日期")
    y, m = (today.year, today.month) if default_year >= today.year else (default_year, 12)
    for _ in range(36):
        if tx.day <= calendar.monthrange(y, m)[1]:
            d = date(y, m, tx.day)
            if d <= today and d.weekday() == tx.weekday:
                return Resolved(datetime(y, m, tx.day, *time), True)
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    raise ValueError(f"无法推断 {tx.day} 日所在的月份")
