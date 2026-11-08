"""Opening balances, monthly rates, and the two reports computed from them."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import books

router = APIRouter()


@router.get("/api/opening")
def opening():
    return books.opening()


class MonthBody(BaseModel):
    month: str | None = None


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


@router.put("/api/rates/{month}/{currency}")
def set_rate(month: str, currency: str, body: RateBody):
    books.set_rate(month, currency, body.rate)
    return {"ok": True}


@router.get("/api/balances")
def balance_sheet(month: str | None = None):
    """Month-end balances of every account and currency, converted to the base currency."""
    return books.balances(month)


@router.get("/api/monthly")
def monthly_report(month: str | None = None):
    """One month's income and expense by category and currency, converted, with the FX gain."""
    return books.monthly(month)
