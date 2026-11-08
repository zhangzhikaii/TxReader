"""TxReader web app: upload app screenshots or read notification emails, review the rows, keep
them, export CSV.

This file only puts the app together: the login check, turning errors into responses, and the
routers in txreader/routes/."""
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

# Settings come from .env (see .env.example); variables already set in the environment win
load_dotenv(Path(__file__).parent / ".env")

from txreader import auth, errors, lists, mail  # noqa: E402
from txreader.routes import books, email, pages, review, settings, transactions  # noqa: E402

app = FastAPI(title="TxReader")
PUBLIC_PATHS = {"/login", "/api/login"}

for module in (pages, review, transactions, books, settings, email):
    app.include_router(module.router)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if (auth.auth_enabled() and request.url.path not in PUBLIC_PATHS
            and not auth.session_valid(request.cookies.get(auth.COOKIE))):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# ---------- errors -> responses (the most specific class wins) ----------

def _detail(status: int):
    return lambda request, exc: JSONResponse({"detail": str(exc)}, status_code=status)


app.add_exception_handler(ValueError, _detail(400))      # something the user can fix
app.add_exception_handler(mail.MailError, _detail(400))  # settings, sign-in, the mail server
app.add_exception_handler(auth.LoginError, _detail(401))


@app.exception_handler(errors.NotFound)
def not_found(request: Request, exc: errors.NotFound):
    return JSONResponse({"detail": exc.detail}, status_code=404)


@app.exception_handler(lists.UnknownNames)
def unknown_names(request: Request, exc: lists.UnknownNames):
    # New accounts / currencies / categories: the page asks, then sends the request again
    return JSONResponse({"detail": {"message": str(exc), "unknown": exc.unknown}}, status_code=409)
