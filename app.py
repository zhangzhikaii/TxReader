"""TxReader web app: upload app screenshots or read notification emails, review the rows, keep
them, export CSV."""
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

from txreader import auth, books, lists, mail, store  # noqa: E402
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
    # (see docs/plan.md, 以后再定).
    return (request.client.host if request.client else None), request.headers.get("user-agent")


# ---------- pages & login ----------

@app.get("/")
def index(request: Request):
    # The site root may be registered in Azure as the email sign-in redirect: pass the code on
    if "state" in request.query_params and ("code" in request.query_params or "error" in request.query_params):
        return RedirectResponse(f"/api/email/callback?{request.url.query}", status_code=303)
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

def _check_source(source: str, email_ok: bool = False):
    if source not in PARSERS and not (email_ok and source == "email"):
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
    _check_source(source, email_ok=True)
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
def transactions(source: str | None = None, date_from: str | None = None, date_to: str | None = None,
                 account: str | None = None, currency: str | None = None, category: str | None = None):
    """source: a screenshot source key, or "manual"."""
    return {"transactions": store.list_transactions(source, date_from, date_to, account, currency, category)}


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


@app.post("/api/transactions")
def add_transactions(body: ManualBody):
    """Transactions typed in by hand, stored right away."""
    try:
        ids = store.add_manual([r.model_dump() for r in body.rows], body.add_accounts)
    except lists.UnknownNames as e:
        raise HTTPException(409, {"message": str(e), "unknown": e.unknown})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ids": ids}


@app.patch("/api/transactions/{tx_id}")
def edit_transaction(tx_id: int, body: TransactionEdit):
    try:
        store.update_transaction(tx_id, body.model_dump(exclude={"add_category"}), body.add_category)
    except lists.UnknownNames as e:
        raise HTTPException(409, {"message": str(e), "unknown": e.unknown})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/suggestions")
def suggestions():
    return store.suggestions()


# ---------- opening balances & monthly rates ----------

@app.exception_handler(books.NotFound)
def books_not_found(request: Request, exc: books.NotFound):
    return JSONResponse({"detail": f"没有币种「{exc}」，请刷新页面"}, status_code=404)


@app.get("/api/opening")
def opening():
    return books.opening()


class MonthBody(BaseModel):
    month: str | None = None


@app.put("/api/opening/start-month")
def set_start_month(body: MonthBody):
    try:
        books.set_start_month(body.month)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class AmountBody(BaseModel):
    amount: float | None = None


@app.put("/api/opening/{account_id}/{currency}")
def set_opening(account_id: int, currency: str, body: AmountBody):
    try:
        books.set_opening(account_id, currency, body.amount)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/rates")
def rates():
    return books.rates()


class RateBody(BaseModel):
    rate: float | None = None


@app.put("/api/rates/{month}/{currency}")
def set_rate(month: str, currency: str, body: RateBody):
    try:
        books.set_rate(month, currency, body.rate)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/balances")
def balance_sheet(month: str | None = None):
    """Month-end balances of every account and currency, converted to the base currency."""
    try:
        return books.balances(month)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/monthly")
def monthly_report(month: str | None = None):
    """One month's income and expense by category and currency, converted, with the FX gain."""
    try:
        return books.monthly(month)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- settings: account / currency / category lists ----------

@app.exception_handler(lists.NotFound)
def list_item_not_found(request: Request, exc: lists.NotFound):
    return JSONResponse({"detail": "找不到这一项，可能已被删除，请刷新页面"}, status_code=404)


@app.get("/api/settings")
def settings():
    return lists.settings()


class NameBody(BaseModel):
    name: str


@app.post("/api/settings/{kind}")
def add_list_item(kind: str, body: NameBody):
    try:
        return {"id": lists.add(kind, body.name)}
    except ValueError as e:
        raise HTTPException(400, str(e))


class ListItemEdit(BaseModel):
    name: str | None = None


@app.patch("/api/settings/{kind}/{item_id}")
def edit_list_item(kind: str, item_id: int, body: ListItemEdit):
    try:
        lists.update(kind, item_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/settings/{kind}/{item_id}")
def delete_list_item(kind: str, item_id: int):
    try:
        lists.delete(kind, item_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/settings/{kind}/order")
def reorder_list(kind: str, body: IdsBody):
    try:
        lists.reorder(kind, body.ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/settings/accounts/{account_id}/currencies")
def add_account_currency(account_id: int, body: NameBody):
    try:
        lists.add_account_currency(account_id, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/settings/accounts/{account_id}/currencies/{currency}")
def remove_account_currency(account_id: int, currency: str):
    try:
        lists.remove_account_currency(account_id, currency)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------- email import ----------

@app.exception_handler(mail.MailError)
def mail_error(request: Request, exc: mail.MailError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(mail.NotFound)
def mail_not_found(request: Request, exc: mail.NotFound):
    return JSONResponse({"detail": "找不到这一项，可能已被删除，请刷新页面"}, status_code=404)


def _default_redirect(request: Request) -> str:
    return str(request.url_for("email_callback"))


def _redirect_uri(request: Request) -> str:
    # Has to be registered in the Azure app exactly as shown on the settings page
    return mail.redirect_uri(_default_redirect(request))


@app.get("/api/email")
def email_settings(request: Request):
    return mail.settings(_default_redirect(request))


class EmailCredentials(BaseModel):
    client_id: str
    client_secret: str | None = None  # None: keep the saved one
    redirect_uri: str | None = None   # None: keep; blank: the default


@app.put("/api/email/credentials")
def set_email_credentials(body: EmailCredentials):
    try:
        mail.set_credentials(body.client_id, body.client_secret, body.redirect_uri)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class RefreshTokenBody(BaseModel):
    refresh_token: str


@app.put("/api/email/token")
def set_email_token(body: RefreshTokenBody):
    """A refresh token pasted by hand, instead of signing in through the site."""
    try:
        mail.set_refresh_token(body.refresh_token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/email/authorize")
def email_authorize(request: Request):
    """Opened by the browser: on to Microsoft's sign-in page, which comes back to the callback."""
    try:
        return RedirectResponse(mail.authorize_url(_redirect_uri(request)), status_code=303)
    except mail.MailError as e:
        return RedirectResponse("/?email_error=" + quote(str(e)) + "#settings-email", status_code=303)


@app.get("/api/email/callback", name="email_callback")
def email_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   error_description: str = ""):
    try:
        if error or not code:
            raise mail.MailError(f"没有完成授权：{(error_description or error or '没有收到授权码').splitlines()[0]}")
        mail.finish_authorize(code, state, _redirect_uri(request))
    except mail.MailError as e:
        return RedirectResponse("/?email_error=" + quote(str(e)) + "#settings-email", status_code=303)
    return RedirectResponse("/?email_connected=1#settings-email", status_code=303)


@app.post("/api/email/disconnect")
def email_disconnect():
    mail.disconnect()
    return {"ok": True}


class EmailRule(BaseModel):
    account: str | None = None
    currency: str | None = None
    category: str | None = None
    sender: str | None = None
    subject: str | None = None
    date_re: str | None = None
    merchant_re: str | None = None
    amount_re: str | None = None


@app.post("/api/email/rules")
def add_email_rule(body: EmailRule):
    try:
        return {"id": mail.add_rule(body.model_dump(exclude_none=True))}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/email/rules/{rule_id}")
def edit_email_rule(rule_id: int, body: EmailRule):
    try:
        mail.update_rule(rule_id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/email/rules/{rule_id}")
def delete_email_rule(rule_id: int):
    mail.delete_rule(rule_id)
    return {"ok": True}


class EmailTest(EmailRule):
    text: str


@app.post("/api/email/test")
def test_email_rule(body: EmailTest):
    """Try regexes on pasted email text, without saving anything."""
    try:
        return mail.try_rule(body.model_dump(), body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))


class FetchBody(BaseModel):
    start: str | None = None  # YYYY-MM-DD, only for the very first update


@app.post("/api/email/fetch")
def email_fetch(body: FetchBody):
    """Read the emails received since the last update; recognized ones become pending rows."""
    try:
        result = mail.fetch(body.start)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**result, "pending": store.list_pending("email")}


@app.get("/api/email/unrecognized")
def email_unrecognized():
    return {"emails": mail.unrecognized()}


@app.post("/api/email/retry")
def email_retry():
    """Read the unrecognized emails again with the rules as they are now."""
    return {**mail.retry(), "pending": store.list_pending("email")}


@app.get("/api/emails/{email_id}")
def email_text(email_id: int):
    return mail.email(email_id)


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
