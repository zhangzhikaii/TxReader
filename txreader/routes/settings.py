"""The settings page's account / currency / category lists."""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import lists
from . import IdsBody

router = APIRouter(prefix="/api/settings")


@router.get("")
def settings():
    return lists.settings()


class NameBody(BaseModel):
    name: str


@router.post("/{kind}")
def add_list_item(kind: str, body: NameBody):
    return {"id": lists.add(kind, body.name)}


class ListItemEdit(BaseModel):
    name: str | None = None


@router.patch("/{kind}/{item_id}")
def edit_list_item(kind: str, item_id: int, body: ListItemEdit):
    lists.update(kind, item_id, body.model_dump())
    return {"ok": True}


@router.delete("/{kind}/{item_id}")
def delete_list_item(kind: str, item_id: int):
    lists.delete(kind, item_id)
    return {"ok": True}


@router.post("/{kind}/order")
def reorder_list(kind: str, body: IdsBody):
    lists.reorder(kind, body.ids)
    return {"ok": True}


@router.post("/accounts/{account_id}/currencies")
def add_account_currency(account_id: int, body: NameBody):
    lists.add_account_currency(account_id, body.name)
    return {"ok": True}


@router.delete("/accounts/{account_id}/currencies/{currency}")
def remove_account_currency(account_id: int, currency: str):
    lists.remove_account_currency(account_id, currency)
    return {"ok": True}
