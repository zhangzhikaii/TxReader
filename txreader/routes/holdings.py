"""持仓: what the investment accounts hold, typed in when it changes, and what it is worth each day."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import portfolio

router = APIRouter(prefix="/api/holdings")


@router.get("")
def holdings(account_id: int | None = None, day: str | None = None):
    return portfolio.holdings(account_id, day)


class Position(BaseModel):
    kind: str
    name: str = ""
    code: str = ""
    currency: str
    quantity: float | str | None = None
    price: float | str | None = None
    unit_cost: float | str | None = None  # 成本价, optional
    amount: float | str | None = None
    symbol: str = ""                # Yahoo Finance symbol; "" = price typed in


class Snapshot(BaseModel):
    account_id: int
    day: str
    currency: str | None = None        # the account's currency; left out: unchanged
    rows: list[Position] | None = None  # left out: unchanged


@router.put("")
def save_holdings(body: Snapshot):
    """The holdings from a day on (replacing that day's). All or nothing."""
    portfolio.save(body.account_id, body.day, None if body.rows is None else [r.model_dump() for r in body.rows],
                   body.currency)
    return {"ok": True}


@router.delete("")
def delete_holdings(account_id: int, day: str):
    """Remove one day's holdings: the ones before hold on."""
    portfolio.delete(account_id, day)
    return {"ok": True}


class Account(BaseModel):
    account_id: int


@router.post("/sync")
def sync(body: Account):
    """When the page is opened: the daily prices and rates the account needs (Yahoo Finance),
    unless fetched lately."""
    return portfolio.sync(body.account_id)


@router.post("/refresh")
def refresh(body: Account):
    """「更新价格」: fetch again now."""
    return portfolio.refresh(body.account_id)


@router.get("/lookup")
def lookup(code: str, day: str):
    """Yahoo Finance symbols for a code, with their price on the day."""
    if not code.strip():
        raise ValueError("先填代码或名称")
    return {"candidates": portfolio.lookup(code, day)}
