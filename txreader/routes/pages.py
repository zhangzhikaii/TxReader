"""The two pages, login and logout, the sign-in record."""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .. import auth

router = APIRouter()
STATIC = Path(__file__).parents[2] / "static"


def _client(request: Request) -> tuple[str | None, str | None]:
    # The connecting address. Behind a reverse proxy this becomes the proxy's address
    # (see docs/plan.md, 以后再定).
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.get("/")
def index(request: Request):
    # The site root may be registered in Azure as the email sign-in redirect: pass the code on
    if "state" in request.query_params and ("code" in request.query_params or "error" in request.query_params):
        return RedirectResponse(f"/api/email/callback?{request.url.query}", status_code=303)
    return FileResponse(STATIC / "index.html")


@router.get("/login")
def login_page(request: Request):
    if not auth.auth_enabled() or auth.session_valid(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


class LoginBody(BaseModel):
    password: str


@router.post("/api/login")
def login(body: LoginBody, request: Request):
    token = auth.login(body.password, *_client(request))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    return resp


@router.post("/api/logout")
def logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.post("/api/logout-all")
def logout_all():
    resp = JSONResponse({"ended": auth.logout_all()})
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.get("/api/security")
def security(request: Request):
    return {"auth": auth.auth_enabled(), **auth.security_overview(request.cookies.get(auth.COOKIE))}
