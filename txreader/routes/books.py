"""Opening balances, monthly rates, and the two reports computed from them."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import books, portfolio

router = APIRouter()


@router.get("/api/opening")
def opening():
    return books.opening()


class MonthBody(BaseModel):
    month: str | None = None


class OpeningCell(BaseModel):
    account_id: int
    currency: str
    amount: float | None = None


class OpeningEdits(BaseModel):
    start_month: str | None = None  # left out: unchanged; null: cleared
    balances: list[OpeningCell] = []


@router.put("/api/opening")
def save_opening(body: OpeningEdits):
    """The start month and several balances, saved together: all or nothing."""
    books.save_opening([b.model_dump() for b in body.balances], body.start_month,
                       set_start="start_month" in body.model_fields_set)
    return {"ok": True}


@router.put("/api/opening/start-month")
def set_start_month(body: MonthBody):
    books.set_start_month(body.month)
    return {"ok": True}


class AmountBody(BaseModel):
    amount: float | None = None


@router.put("/api/opening/{account_id}/{currency}")
def set_opening(account_id: int, currency: str, body: AmountBody):
    books.set_opening(account_id, currency, body.amount)
    return {"ok": True}


@router.get("/api/rates")
def rates():
    return books.rates()


class RateBody(BaseModel):
    rate: float | None = None


class RateCell(BaseModel):
    month: str
    currency: str
    rate: float | None = None


class RateEdits(BaseModel):
    rates: list[RateCell]


@router.put("/api/rates")
def save_rates(body: RateEdits):
    """Several rates saved together: all or nothing."""
    books.save_rates([r.model_dump() for r in body.rates])
    return {"ok": True}


@router.put("/api/rates/{month}/{currency}")
def set_rate(month: str, currency: str, body: RateBody):
    books.set_rate(month, currency, body.rate)
    return {"ok": True}


@router.get("/api/balances")
def balance_sheet(month: str | None = None):
    """Month-end balances of every account and currency, converted to the base currency; with
    the investment accounts' latest 持仓 valuation by then (account name -> see portfolio.at_month_end)."""
    out = books.balances(month)
    out["valuations"] = portfolio.at_month_end(out["month"]) if out["month"] else {}
    return out


@router.get("/api/monthly")
def monthly_report(month: str | None = None, year: str | None = None):
    """One month's (or year's) income and expense by category and currency, converted, with the FX gain."""
    return books.monthly(month, year=year)


@router.get("/api/trend")
def trend(end: str | None = None, span: int = 6, currency: str | None = None):
    """Recurring categories month by month, in one currency or all converted to the base."""
    return books.trend(end, span, currency)
