"""SQLite database and screenshot storage.

Everything lives under one data directory (env TXREADER_DATA, default ./data):
    txreader.db               the database
    images/<ab>/<sha256>.webp  uploaded screenshots, re-encoded as WebP

Read emails are kept in the database (table emails), not as files.
"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

DEFAULT_DATA = Path(__file__).parent.parent / "data"
WEBP_QUALITY = 80
WEBP_MAX_SIDE = 16383  # WebP can't store anything larger

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT NOT NULL UNIQUE,   -- of the uploaded file's bytes
    filename    TEXT NOT NULL,
    source      TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    path        TEXT NOT NULL,          -- relative to the data directory
    parsed      TEXT                    -- JSON: raw parser output, reused when the same file comes again
);

-- Rows recognized from a screenshot (image_id) or read from an email (email_id, source 'email')
CREATE TABLE IF NOT EXISTS ocr_rows (
    id           INTEGER PRIMARY KEY,
    image_id     INTEGER REFERENCES images(id),
    email_id     INTEGER REFERENCES emails(id),
    pos          INTEGER NOT NULL,      -- order within the screenshot, top to bottom
    source       TEXT NOT NULL,
    -- values as recognized, never edited (used to spot rows seen before)
    date         TEXT NOT NULL,         -- 2026/10/29 22:12:00
    amount       REAL NOT NULL,
    merchant     TEXT NOT NULL,
    balance      REAL,
    sign_guessed INTEGER NOT NULL DEFAULT 0,
    date_guessed INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed')),
    suspect_of   INTEGER REFERENCES ocr_rows(id) ON DELETE SET NULL,  -- same date + amount, other merchant
    draft        TEXT,                  -- JSON: edits made while pending
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ocr_rows_source ON ocr_rows(source, status);

-- Email import (settings page): which emails to read and how to pick the values out of them.
-- The regexes have one group each; the amount is stored as an expense.
CREATE TABLE IF NOT EXISTS email_rules (
    id          INTEGER PRIMARY KEY,
    account     TEXT NOT NULL,
    currency    TEXT NOT NULL,
    category    TEXT NOT NULL,
    sender      TEXT NOT NULL,          -- address, or @domain for any address there
    subject     TEXT NOT NULL DEFAULT '',  -- the subject must contain this (blank: any)
    date_re     TEXT NOT NULL,
    merchant_re TEXT NOT NULL,
    amount_re   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Every email a rule picked up, recognized or not, so it is never read twice
CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY,
    message_id  TEXT NOT NULL UNIQUE,   -- the mail server's id
    rule_id     INTEGER REFERENCES email_rules(id) ON DELETE SET NULL,
    received_at TEXT NOT NULL,          -- UTC, as the server gives it
    sender      TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,          -- plain text
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    origin       TEXT NOT NULL DEFAULT 'screenshot' CHECK (origin IN ('screenshot', 'manual', 'email')),
    ocr_row_id   INTEGER UNIQUE REFERENCES ocr_rows(id),  -- set for screenshot rows only
    date         TEXT NOT NULL,
    weekday      TEXT NOT NULL,
    account      TEXT NOT NULL,
    currency     TEXT NOT NULL,
    amount       REAL NOT NULL,
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions(date);

-- The account / currency / category lists (managed on the settings page). `sort` is the order
-- they are shown in. An account may be used with several currencies (account_currencies).
CREATE TABLE IF NOT EXISTS accounts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS currencies (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort       INTEGER NOT NULL DEFAULT 0,
    is_base    INTEGER NOT NULL DEFAULT 0,  -- the one everything is converted into (at most one)
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_currencies (
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    currency_id INTEGER NOT NULL REFERENCES currencies(id),
    PRIMARY KEY (account_id, currency_id)
);

-- Single values: start_month (YYYY-MM), the month whose end the opening balances are for;
-- email_* for the email import (see txreader/mail.py)
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- What each account held in each currency at the end of the start month
CREATE TABLE IF NOT EXISTS opening_balances (
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    currency_id INTEGER NOT NULL REFERENCES currencies(id),
    amount      REAL NOT NULL,
    PRIMARY KEY (account_id, currency_id)
);

-- Monthly rates, typed in by hand: 1 unit of the currency = rate units of the base currency
CREATE TABLE IF NOT EXISTS fx_rates (
    month       TEXT NOT NULL,          -- 2026-10
    currency_id INTEGER NOT NULL REFERENCES currencies(id),
    rate        REAL NOT NULL CHECK (rate > 0),
    PRIMARY KEY (month, currency_id)
);

CREATE TABLE IF NOT EXISTS exports (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    filename TEXT NOT NULL,
    count    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS export_items (
    export_id      INTEGER NOT NULL REFERENCES exports(id),
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    PRIMARY KEY (export_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS export_items_tx ON export_items(transaction_id);

CREATE TABLE IF NOT EXISTS login_events (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT,
    success    INTEGER NOT NULL,
    reason     TEXT NOT NULL            -- ok / bad_password / locked / not_configured
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,    -- sha256 of the cookie value
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,           -- hard limit, however active the session is
    last_seen_at TEXT,                  -- last request; idle too long and the session ends
    ip         TEXT,
    user_agent TEXT
);
"""

_initialized: set[Path] = set()


def data_dir() -> Path:
    return Path(os.environ.get("TXREADER_DATA") or DEFAULT_DATA)


def now() -> str:
    """UTC timestamp for *_at columns."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rebuild(conn, table: str, kept: str, indexes: str = "") -> None:
    """Recreate a table from its definition in SCHEMA, keeping the `kept` columns. SQLite can't
    change a column's constraints in place."""
    create = SCHEMA[SCHEMA.index(f"CREATE TABLE IF NOT EXISTS {table} ("):]
    create = create[:create.index(");") + 2].replace(f"IF NOT EXISTS {table} (", f"{table}_new (")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # other tables point at this one
    conn.executescript(
        f"BEGIN; {create}"
        f" INSERT INTO {table}_new ({kept}) SELECT {kept} FROM {table};"
        f" DROP TABLE {table}; ALTER TABLE {table}_new RENAME TO {table}; {indexes} COMMIT;")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate(conn) -> None:
    """Bring databases created by older versions up to SCHEMA."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "last_seen_at" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT")
        conn.execute("UPDATE sessions SET last_seen_at = created_at")

    # transactions gained `origin` and a nullable ocr_row_id (manual entries have no screenshot).
    # SQLite can't drop NOT NULL in place, so the table is rebuilt from the definition in SCHEMA.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "origin" not in cols:
        _rebuild(conn, "transactions",
                 "id, ocr_row_id, date, weekday, account, currency, amount, category, description,"
                 " confirmed_at, updated_at",
                 "CREATE INDEX IF NOT EXISTS transactions_date ON transactions(date);")

    # ocr_rows also hold rows read from emails: image_id became nullable, email_id was added
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ocr_rows)")}
    if "email_id" not in cols:
        _rebuild(conn, "ocr_rows",
                 "id, image_id, pos, source, date, amount, merchant, balance, sign_guessed, date_guessed,"
                 " status, suspect_of, draft, created_at",
                 "CREATE INDEX IF NOT EXISTS ocr_rows_source ON ocr_rows(source, status);")

    # The lists gained an order and a base currency. An `archived` column existed briefly
    # during development and was dropped again.
    for table in ("accounts", "currencies", "categories"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "sort" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN sort INTEGER NOT NULL DEFAULT 0")
            conn.execute(f"UPDATE {table} SET sort = id")
        if table == "currencies" and "is_base" not in cols:
            conn.execute("ALTER TABLE currencies ADD COLUMN is_base INTEGER NOT NULL DEFAULT 0")
        if "archived" in cols:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN archived")

    # The lists started out empty: fill them once from the transactions so far
    if not conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        register_accounts(conn, conn.execute(
            "SELECT DISTINCT account, currency FROM transactions ORDER BY id").fetchall())
    if not conn.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
        register_accounts(conn, [], [r[0] for r in conn.execute(
            "SELECT category FROM transactions GROUP BY category ORDER BY MIN(id)")])
    default_base(conn)


def _add_name(conn, table: str, name: str, t: str) -> None:
    """Add a name to a list (at the end) unless it is already there."""
    conn.execute(f"INSERT OR IGNORE INTO {table} (name, sort, created_at)"
                 f" VALUES (?, (SELECT COALESCE(MAX(sort), 0) + 1 FROM {table}), ?)", (name, t))


def register_accounts(conn, pairs, categories=()) -> None:
    """Add (account, currency) pairs and categories that aren't on the lists yet; blank names
    are ignored."""
    t = now()
    for name in categories:
        if (name or "").strip():
            _add_name(conn, "categories", name.strip(), t)
    for account, currency in pairs:
        account, currency = (account or "").strip(), (currency or "").strip()
        if account:
            _add_name(conn, "accounts", account, t)
        if currency:
            _add_name(conn, "currencies", currency, t)
        if account and currency:
            conn.execute(
                "INSERT OR IGNORE INTO account_currencies (account_id, currency_id)"
                " SELECT a.id, c.id FROM accounts a, currencies c WHERE a.name = ? AND c.name = ?",
                (account, currency))
    default_base(conn)


def default_base(conn) -> None:
    """人民币 is the base currency unless another one was chosen."""
    conn.execute("UPDATE currencies SET is_base = 1 WHERE name = '人民币'"
                 " AND NOT EXISTS (SELECT 1 FROM currencies WHERE is_base = 1)")


@contextmanager
def connect():
    """A connection that commits on success and rolls back on error."""
    root = data_dir()
    path = root / "txreader.db"
    if path not in _initialized:
        root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if path not in _initialized:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            _migrate(conn)
            _initialized.add(path)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_image(img: np.ndarray, digest: str) -> str:
    """Store a screenshot as WebP; returns its path relative to the data directory."""
    h, w = img.shape[:2]
    if max(h, w) > WEBP_MAX_SIDE:
        scale = WEBP_MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, WEBP_QUALITY])
    if not ok:
        raise ValueError("无法保存图片")
    rel = f"images/{digest[:2]}/{digest}.webp"
    path = data_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(buf.tobytes())
    tmp.replace(path)
    return rel


def image_path(rel: str) -> Path:
    return data_dir() / rel
