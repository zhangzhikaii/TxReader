"""Kept transactions: listing, typing in by hand, editing, exporting."""

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from .. import store

router = APIRouter()


@router.get("/api/transactions")
def transactions(source: str | None = None, date_from: str | None = None, date_to: str | None = None,
                 account: str | None = None, currency: str | None = None, category: str | None = None):
    """source: a screenshot source key, "email", or "manual"."""
    return {"transactions": store.list_transactions(source, date_from, date_to, account, currency, category)}


@router.get("/api/transactions/export")
def export(source: str | None = None, date_from: str | None = None, date_to: str | None = None,
           account: str | None = None, currency: str | None = None, category: str | None = None):
    """The transactions the same filters list, as CSV."""
    rows = store.list_transactions(source, date_from, date_to, account, currency, category)
    return Response(store.export_csv(rows).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="txreader.csv"'})


class TransactionFields(BaseModel):
    date: str | None = None
    account: str | None = None
    currency: str | None = None
    amount: float | None = None
    category: str | None = None
    description: str | None = None


class TransactionEdit(BaseModel):
    category: str | None = None
    description: str | None = None
    add_category: bool = False  # the user agreed to add a category not on the list yet


class ManualBody(BaseModel):
    rows: list[TransactionFields]
    add_accounts: bool = False  # the user agreed to add accounts / currencies not on the lists yet


@router.post("/api/transactions")
def add_transactions(body: ManualBody):
    """Transactions typed in by hand, stored right away."""
    return {"ids": store.add_manual([r.model_dump() for r in body.rows], body.add_accounts)}


@router.patch("/api/transactions/{tx_id}")
def edit_transaction(tx_id: int, body: TransactionEdit):
    store.update_transaction(tx_id, body.model_dump(exclude={"add_category"}), body.add_category)
    return {"ok": True}


class TransactionBatchEdit(BaseModel):
    id: int
    category: str | None = None
    description: str | None = None


class TransactionBatch(BaseModel):
    edits: list[TransactionBatchEdit]
    add_categories: bool = False  # the user agreed to add categories not on the list yet


@router.patch("/api/transactions")
def edit_transactions(body: TransactionBatch):
    """Edits saved together from the transactions page: all of them, or none."""
    store.update_transactions([e.model_dump(exclude_none=True) for e in body.edits], body.add_categories)
    return {"ok": True}


@router.get("/api/suggestions")
def suggestions():
    return store.suggestions()
