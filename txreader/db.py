"""SQLite database and screenshot storage.

Everything lives under one data directory (env TXREADER_DATA, default ./data):
    txreader.db               the database
    images/<ab>/<sha256>.webp  uploaded screenshots, re-encoded as WebP
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

CREATE TABLE IF NOT EXISTS ocr_rows (
    id           INTEGER PRIMARY KEY,
    image_id     INTEGER NOT NULL REFERENCES images(id),
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

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    ocr_row_id   INTEGER NOT NULL UNIQUE REFERENCES ocr_rows(id),
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
    expires_at TEXT NOT NULL,
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
