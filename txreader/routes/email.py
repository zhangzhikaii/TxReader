"""Email import: Outlook sign-in, the rules, reading new emails."""
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import mail, store

router = APIRouter()


def _default_redirect(request: Request) -> str:
    return str(request.url_for("email_callback"))


def _redirect_uri(request: Request) -> str:
    # Has to be registered in the Azure app exactly as shown on the settings page
    return mail.redirect_uri(_default_redirect(request))


def _back_with_error(e: mail.MailError) -> RedirectResponse:
    return RedirectResponse("/?email_error=" + quote(str(e)) + "#settings-email", status_code=303)


@router.get("/api/email")
def email_settings(request: Request):
    return mail.settings(_default_redirect(request))


class EmailCredentials(BaseModel):
    client_id: str
    client_secret: str | None = None  # None: keep the saved one
    redirect_uri: str | None = None   # None: keep; blank: the default


@router.put("/api/email/credentials")
def set_email_credentials(body: EmailCredentials):
    mail.set_credentials(body.client_id, body.client_secret, body.redirect_uri)
    return {"ok": True}


class RefreshTokenBody(BaseModel):
    refresh_token: str


@router.put("/api/email/token")
def set_email_token(body: RefreshTokenBody):
    """A refresh token pasted by hand, instead of signing in through the site."""
    mail.set_refresh_token(body.refresh_token)
    return {"ok": True}


@router.get("/api/email/authorize")
def email_authorize(request: Request):
    """Opened by the browser: on to Microsoft's sign-in page, which comes back to the callback."""
    try:
        return RedirectResponse(mail.authorize_url(_redirect_uri(request)), status_code=303)
    except mail.MailError as e:
        return _back_with_error(e)


@router.get("/api/email/callback", name="email_callback")
def email_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   error_description: str = ""):
    try:
        if error or not code:
            raise mail.MailError(f"没有完成授权：{(error_description or error or '没有收到授权码').splitlines()[0]}")
        mail.finish_authorize(code, state, _redirect_uri(request))
    except mail.MailError as e:
        return _back_with_error(e)
    return RedirectResponse("/?email_connected=1#settings-email", status_code=303)


@router.post("/api/email/disconnect")
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


@router.post("/api/email/rules")
def add_email_rule(body: EmailRule):
    return {"id": mail.add_rule(body.model_dump(exclude_none=True))}


@router.patch("/api/email/rules/{rule_id}")
def edit_email_rule(rule_id: int, body: EmailRule):
    mail.update_rule(rule_id, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/api/email/rules/{rule_id}")
def delete_email_rule(rule_id: int):
    mail.delete_rule(rule_id)
    return {"ok": True}


class EmailTest(EmailRule):
    text: str


@router.post("/api/email/test")
def test_email_rule(body: EmailTest):
    """Try regexes on pasted email text, without saving anything."""
    return mail.try_rule(body.model_dump(), body.text)


class FetchBody(BaseModel):
    start: str | None = None  # YYYY-MM-DD, only for the very first update


@router.post("/api/email/fetch")
def email_fetch(body: FetchBody):
    """Read the emails received since the last update; recognized ones become pending rows."""
    return {**mail.fetch(body.start), "pending": store.list_pending("email")}


@router.get("/api/email/unrecognized")
def email_unrecognized():
    return {"emails": mail.unrecognized()}


@router.post("/api/email/retry")
def email_retry():
    """Read the unrecognized emails again with the rules as they are now."""
    return {**mail.retry(), "pending": store.list_pending("email")}


@router.get("/api/emails/{email_id}")
def email_text(email_id: int):
    return mail.email(email_id)
