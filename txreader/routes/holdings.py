"""持仓: weekly snapshots of the investment accounts and what they are worth."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import portfolio

router = APIRouter(prefix="/api/holdings")


@router.get("")
def holdings(account_id: int | None = None, week: str | None = None):
    return portfolio.holdings(account_id, week)


class Position(BaseModel):
    kind: str
    name: str = ""
    code: str = ""
    currency: str
    quantity: float | str | None = None
    price: float | str | None = None
    unit_cost: float | str | None = None  # 成本价, optional
    amount: float | str | None = None


class Snapshot(BaseModel):
    account_id: int
    week: str
    currency: str | None = None  # the account's currency; left out: unchanged
    rows: list[Position]


@router.put("")
def save_holdings(body: Snapshot):
    """Replace one week's snapshot (no rows: delete it). All or nothing."""
    portfolio.save(body.account_id, body.week, [r.model_dump() for r in body.rows], body.currency)
    return {"ok": True}
