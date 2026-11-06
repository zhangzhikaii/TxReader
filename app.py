"""TxReader web app: upload app screenshots, review the rows, keep them, export CSV."""
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

# Settings come from .env (see .env.example); variables already set in the environment win
load_dotenv(Path(__file__).parent / ".env")

from txreader import auth, store  # noqa: E402
from txreader.db import image_path  # noqa: E402
from txreader.parsers import PARSERS  # noqa: E402

app = FastAPI(title="TxReader")
STATIC = Path(__file__).parent / "static"
PUBLIC_PATHS = {"/login", "/api/login"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    if (auth.auth_enabled() and request.url.path not in PUBLIC_PATHS
            and not auth.session_valid(request.cookies.get(auth.COOKIE))):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.exception_handler(store.NotFound)
def not_found(request: Request, exc: store.NotFound):
    return JSONResponse({"detail": "找不到这条记录，可能已被删除或已入库，请刷新页面"}, status_code=404)


def _client(request: Request) -> tuple[str | None, str | None]:
    # The connecting address. Behind a reverse proxy this becomes the proxy's address
    # (see docs/cloud-plan.md, 以后再定).
    return (request.client.host if request.client else None), request.headers.get("user-agent")


# ---------- pages & login ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page(request: Request):
    if not auth.auth_enabled() or auth.session_valid(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    try:
        token = auth.login(body.password, *_client(request))
    except auth.LoginError as e:
        raise HTTPException(401, str(e))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    return resp


@app.post("/api/logout")
def logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.post("/api/logout-all")
def logout_all():
    resp = JSONResponse({"ended": auth.logout_all()})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/security")
def security(request: Request):
    return {"auth": auth.auth_enabled(), **auth.security_overview(request.cookies.get(auth.COOKIE))}


# ---------- recognize & review ----------

def _check_source(source: str):
    if source not in PARSERS:
        raise HTTPException(400, f"不支持的来源：{source}")


@app.post("/api/parse")
def parse(files: list[UploadFile] = File(...),
          year: int = Form(date.today().year),
          source: str = Form("wechat")):
    _check_source(source)
    try:
        result = store.recognize([(f.filename or "", f.file.read()) for f in files], year, source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**result, "pending": store.list_pending(source)}


@app.get("/api/pending")
def pending(source: str):
    _check_source(source)
    return {"pending": store.list_pending(source), "counts": store.pending_counts()}


@app.patch("/api/pending/{row_id}")
def edit_pending(row_id: int, draft: dict):
    try:
        store.update_draft(row_id, draft)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class IdsBody(BaseModel):
    ids: list[int]


@app.post("/api/pending/delete")
def delete_pending(body: IdsBody):
    return {"deleted": store.delete_pending(body.ids)}


class ConfirmBody(BaseModel):
    rows: list[dict]


@app.post("/api/confirm")
def confirm(body: ConfirmBody):
    try:
        return {"confirmed": store.confirm(body.rows)}
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"提交失败：{e}")


@app.get("/api/images/{image_id}")
def image(image_id: int):
    rel, _ = store.image_file(image_id)
    path = image_path(rel)
    if not path.exists():
        raise HTTPException(404, "截图文件不见了")
    # Content never changes for an id
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000"})


# ---------- transactions & exports ----------

@app.get("/api/transactions")
def transactions(source: str | None = None, date_from: str | None = None, date_to: str | None = None):
    return {"transactions": store.list_transactions(source, date_from, date_to)}


class TransactionEdit(BaseModel):
    category: str | None = None
    description: str | None = None


@app.patch("/api/transactions/{tx_id}")
def edit_transaction(tx_id: int, body: TransactionEdit):
    try:
        store.update_transaction(tx_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class ExportBody(BaseModel):
    ids: list[int]
    filename: str = ""


@app.post("/api/exports")
def export(body: ExportBody):
    name = body.filename if re.fullmatch(r"[\w.-]{1,100}\.csv", body.filename) else "txreader.csv"
    try:
        csv = store.create_export(body.ids, name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(csv.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@app.get("/api/exports")
def exports():
    return {"exports": store.list_exports()}
