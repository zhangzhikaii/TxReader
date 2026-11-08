"""Recognizing screenshots and reviewing pending rows (from screenshots or emails)."""
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import store
from ..db import image_path
from ..parsers import PARSERS
from . import IdsBody

router = APIRouter()


def _check_source(source: str, email_ok: bool = False):
    if source not in PARSERS and not (email_ok and source == "email"):
        raise HTTPException(400, f"不支持的来源：{source}")


@router.post("/api/parse")
def parse(files: list[UploadFile] = File(...),
          year: int = Form(date.today().year),
          source: str = Form("wechat")):
    _check_source(source)
    result = store.recognize([(f.filename or "", f.file.read()) for f in files], year, source)
    return {**result, "pending": store.list_pending(source)}


@router.get("/api/pending")
def pending(source: str):
    _check_source(source, email_ok=True)
    return {"pending": store.list_pending(source), "counts": store.pending_counts()}


@router.patch("/api/pending/{row_id}")
def edit_pending(row_id: int, draft: dict):
    store.update_draft(row_id, draft)
    return {"ok": True}


@router.post("/api/pending/delete")
def delete_pending(body: IdsBody):
    return {"deleted": store.delete_pending(body.ids)}


class ConfirmBody(BaseModel):
    rows: list[dict]


@router.post("/api/confirm")
def confirm(body: ConfirmBody):
    try:
        return {"confirmed": store.confirm(body.rows)}
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"提交失败：{e}")


@router.get("/api/images/{image_id}")
def image(image_id: int):
    rel, _ = store.image_file(image_id)
    path = image_path(rel)
    if not path.exists():
        raise HTTPException(404, "截图文件不见了")
    # Content never changes for an id
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000"})
