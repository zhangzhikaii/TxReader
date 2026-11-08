"""Everything the web app does with the database: recognize, review, confirm, export.

Pending rows come from screenshots (recognize) or emails (txreader/mail.py, source 'email');
both are reviewed and confirmed the same way."""
import json
import re
import threading
from dataclasses import asdict
from datetime import datetime

from . import lists
from .db import connect, now, register_accounts, save_image, sha256
from .matching import match_rows
from .models import Tx
from .ocr import decode_image
from .parsers import parse_image
from .records import WEEKDAYS_JA, build_records

CSV_HEADER = ["日期", "曜日", "账户", "币种", "金额", "种类", "描述"]
DRAFT_KEYS = {"date", "amount", "account", "currency", "category", "merchant"}
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$")

# Matching and inserting must not interleave between two uploads, or both would add the same row
_match_lock = threading.Lock()


class NotFound(LookupError):
    pass


# ---------- recognize ----------

def _ingest(data: bytes, filename: str, source: str) -> tuple[int, list[Tx], list[str]]:
    """Store one screenshot and return its parse. A file seen before (same bytes, same
    source) is not recognized again: its earlier result is reused."""
    digest = sha256(data)
    with connect() as conn:
        old = conn.execute("SELECT id, source, parsed FROM images WHERE sha256 = ?", (digest,)).fetchone()
    if old and old["source"] == source and old["parsed"]:
        parsed = json.loads(old["parsed"])
        return old["id"], [Tx(**t) for t in parsed["txs"]], parsed["warnings"]

    img = decode_image(data)
    txs, warnings = parse_image(img, source)
    parsed = json.dumps({"txs": [asdict(t) for t in txs], "warnings": warnings}, ensure_ascii=False)
    rel = None if old else save_image(img, digest)
    with connect() as conn:
        if old:  # uploaded before under another source: keep the file, replace the parse
            conn.execute("UPDATE images SET source = ?, parsed = ? WHERE id = ?", (source, parsed, old["id"]))
            return old["id"], txs, warnings
        image_id = conn.execute(
            "INSERT INTO images (sha256, filename, source, uploaded_at, path, parsed) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (sha256) DO UPDATE SET source = excluded.source, parsed = excluded.parsed"
            " RETURNING id",
            (digest, filename, source, now(), rel, parsed)).fetchone()[0]
    return image_id, txs, warnings


def recognize(uploads: list[tuple[str, bytes]], year: int, source: str) -> dict:
    """Recognize screenshots and add rows not seen before as pending.

    Raises ValueError when nothing could be read at all.
    """
    image_ids, per_image, errors, images = [], [], [], []
    for name, data in uploads:
        try:
            image_id, txs, warnings = _ingest(data, name, source)
        except ValueError as e:
            errors.append(f"{name}: {e}")
            continue
        image_ids.append(image_id)
        per_image.append(txs)
        images.append({"id": image_id, "name": name})
        errors += [f"{name}: {w}" for w in warnings]
    if not per_image and errors:
        raise ValueError("; ".join(errors))
    records = build_records(per_image, year)
    incoming = [{**r, "date_guessed": r["year_guessed"]} for r in records]

    counts = {"recognized": len(records), "skip": 0, "merge": 0, "suspect": 0, "new": 0}
    skipped, new_ids = [], []
    with _match_lock, connect() as conn:
        existing = [dict(r) for r in conn.execute(
            "SELECT id, date, amount, merchant, balance, date_guessed, status FROM ocr_rows WHERE source = ?",
            (source,))]
        for row, (verdict, match_id) in zip(incoming, match_rows(incoming, existing)):
            counts[verdict] += 1
            if verdict == "skip":
                skipped.append({k: row[k] for k in ("date", "amount", "merchant")})
            if verdict in ("new", "suspect"):
                new_ids.append(conn.execute(
                    "INSERT INTO ocr_rows (image_id, pos, source, date, amount, merchant, balance,"
                    " sign_guessed, date_guessed, suspect_of, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (image_ids[row["img"]], row["img_pos"], source, row["date"], row["amount"],
                     row["merchant"], row["balance"], row["sign_guessed"], row["date_guessed"],
                     match_id if verdict == "suspect" else None, now())).fetchone()[0])
    return {"counts": counts, "skipped": skipped, "new_ids": new_ids,
            "images": images, "errors": errors}


# ---------- pending rows ----------

def _weekday(date: str) -> str:
    return WEEKDAYS_JA[datetime.strptime(date, "%Y/%m/%d %H:%M:%S").weekday()]


def _pending_row(r) -> dict:
    draft = json.loads(r["draft"] or "{}")
    date = draft.get("date", r["date"])
    out = {
        "id": r["id"], "image_id": r["image_id"], "image_name": r["image_name"],
        "email_id": r["email_id"], "email_subject": r["email_subject"],
        "date": date, "weekday": _weekday(date),
        "amount": draft.get("amount", r["amount"]),
        "merchant": draft.get("merchant", r["merchant"]),
        "balance": r["balance"],
        "sign_guessed": bool(r["sign_guessed"]),
        "year_guessed": bool(r["date_guessed"]) and "date" not in draft,  # an edited date has been checked
        "suspect": None,
    }
    for k in ("account", "currency", "category"):
        if k in draft:
            out[k] = draft[k]
    if r["s_id"] is not None:
        out["suspect"] = {"date": r["s_date"], "amount": r["s_amount"], "merchant": r["s_merchant"],
                          "confirmed": r["s_status"] == "confirmed"}
    return out


def list_pending(source: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.*, s.id AS s_id, s.date AS s_date, s.amount AS s_amount,"
            " s.merchant AS s_merchant, s.status AS s_status, i.filename AS image_name,"
            " e.subject AS email_subject"
            " FROM ocr_rows r LEFT JOIN images i ON i.id = r.image_id"
            " LEFT JOIN emails e ON e.id = r.email_id"
            " LEFT JOIN ocr_rows s ON s.id = r.suspect_of"
            " WHERE r.source = ? AND r.status = 'pending'"
            " ORDER BY r.date, r.image_id, r.pos DESC",  # apps list newest first
            (source,)).fetchall()
    return [_pending_row(r) for r in rows]


def pending_counts() -> dict[str, int]:
    with connect() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT source, COUNT(*) FROM ocr_rows WHERE status = 'pending' GROUP BY source")}


def _check_date(date: str) -> str:
    if not isinstance(date, str) or not DATE_RE.match(date):
        raise ValueError(f"日期格式不对：{date}")
    try:
        _weekday(date)
    except ValueError:
        raise ValueError(f"日期不存在：{date}") from None
    return date


def _clean_draft(draft: dict) -> dict:
    out = {}
    for k, v in draft.items():
        if k not in DRAFT_KEYS:
            continue
        if k == "date":
            v = _check_date(v)
        elif k == "amount":
            v = round(float(v), 2)
        else:
            v = str(v)
        out[k] = v
    return out


def update_draft(row_id: int, draft: dict) -> None:
    draft = _clean_draft(draft)
    with connect() as conn:
        r = conn.execute("SELECT draft FROM ocr_rows WHERE id = ? AND status = 'pending'", (row_id,)).fetchone()
        if not r:
            raise NotFound(row_id)
        merged = {**json.loads(r["draft"] or "{}"), **draft}
        conn.execute("UPDATE ocr_rows SET draft = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), row_id))


def delete_pending(ids: list[int]) -> int:
    with connect() as conn:
        return sum(conn.execute("DELETE FROM ocr_rows WHERE id = ? AND status = 'pending'", (i,)).rowcount
                   for i in ids)


def confirm(rows: list[dict]) -> int:
    """Turn pending rows into transactions. Each row carries the reviewed values:
    id, date, account, currency, amount, category, description. All or nothing."""
    with _match_lock, connect() as conn:
        for row in rows:
            date = _check_date(row.get("date"))
            amount = round(float(row["amount"]), 2)
            r = conn.execute("SELECT id, source FROM ocr_rows WHERE id = ? AND status = 'pending'",
                             (row["id"],)).fetchone()
            if not r:
                raise NotFound(row["id"])
            t = now()
            conn.execute(
                "INSERT INTO transactions (origin, ocr_row_id, date, weekday, account, currency, amount, category,"
                " description, confirmed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("email" if r["source"] == "email" else "screenshot", row["id"], date, _weekday(date), str(row.get("account", "")), str(row.get("currency", "")),
                 amount, str(row.get("category", "")), str(row.get("description", "")), t, t))
            conn.execute("UPDATE ocr_rows SET status = 'confirmed' WHERE id = ?", (row["id"],))
        register_accounts(conn, [(r.get("account"), r.get("currency")) for r in rows],
                          [r.get("category") for r in rows])
    return len(rows)


# ---------- transactions ----------

def list_transactions(source: str | None = None, date_from: str | None = None,
                      date_to: str | None = None, account: str | None = None,
                      currency: str | None = None, category: str | None = None) -> list[dict]:
    """source is a screenshot source key, "email", or "manual" for rows typed in by hand.
    date_from / date_to are inclusive, as 2026-10-01 or 2026/10/01.
    account / currency / category match the name exactly."""
    where, args = [], []
    for col, value in (("account", account), ("currency", currency), ("category", category)):
        if value:
            where.append(f"t.{col} = ?")
            args.append(value)
    if source == "manual":
        where.append("t.origin = 'manual'")
    elif source:
        where.append("r.source = ?")
        args.append(source)
    if date_from:
        where.append("substr(t.date, 1, 10) >= ?")
        args.append(date_from.replace("-", "/"))
    if date_to:
        where.append("substr(t.date, 1, 10) <= ?")
        args.append(date_to.replace("-", "/"))
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.*, r.source, r.image_id, r.email_id, i.filename AS image_name,"
            " COUNT(e.id) AS export_count, MAX(e.at) AS last_export"
            " FROM transactions t LEFT JOIN ocr_rows r ON r.id = t.ocr_row_id"
            " LEFT JOIN images i ON i.id = r.image_id"
            " LEFT JOIN export_items ei ON ei.transaction_id = t.id"
            " LEFT JOIN exports e ON e.id = ei.export_id"
            + (" WHERE " + " AND ".join(where) if where else "") +
            " GROUP BY t.id ORDER BY t.date, r.image_id, r.pos DESC, t.id",
            args).fetchall()
    return [dict(r) for r in rows]


TX_FIELDS = ("date", "account", "currency", "amount", "category", "description")
TX_REQUIRED = ("date", "account", "currency", "amount", "category")
TX_EDITABLE = ("category", "description")  # once stored, however it was added, only these can change


def _clean_tx(row: dict, keys) -> dict:
    out = {}
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        if k == "date":
            out["date"] = _check_date(v)
            out["weekday"] = _weekday(v)
        elif k == "amount":
            amount = round(float(v), 2)
            if not amount:
                raise ValueError("金额不能为 0")
            out["amount"] = amount
        else:
            out[k] = str(v).strip()
    return out


def add_manual(rows: list[dict], add_accounts: bool = False) -> list[int]:
    """Store transactions typed in by hand. Each row: date, account, currency, amount,
    category, description. All or nothing.

    Accounts, currencies, account-currency pairs and categories not on the lists yet are added
    only with add_accounts (the user has been asked); otherwise lists.UnknownNames is raised."""
    cleaned = []
    for row in rows:
        tx = _clean_tx(row, TX_FIELDS)
        if any(not tx.get(k) for k in TX_REQUIRED):
            raise ValueError("每一笔都要填日期、账户、币种、金额和种类")
        cleaned.append(tx)
    ids = []
    with connect() as conn:
        unknown = lists.unknown(cleaned, lists.offered(conn))
        if any(unknown.values()):
            if not add_accounts:
                raise lists.UnknownNames(unknown)
            register_accounts(conn, [(tx["account"], tx["currency"]) for tx in cleaned],
                              [tx["category"] for tx in cleaned])
        for tx in cleaned:
            t = now()
            ids.append(conn.execute(
                "INSERT INTO transactions (origin, date, weekday, account, currency, amount, category,"
                " description, confirmed_at, updated_at) VALUES ('manual', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (tx["date"], tx["weekday"], tx["account"], tx["currency"], tx["amount"],
                 tx["category"], tx.get("description", ""), t, t)).fetchone()[0])
    return ids


def update_transaction(tx_id: int, changes: dict, add_category: bool = False) -> None:
    """A category not on the list yet is added only with add_category (the user has been
    asked); otherwise lists.UnknownNames is raised."""
    fields = _clean_tx(changes, TX_EDITABLE)
    if not fields:
        raise ValueError("入库后只能修改种类和描述")
    if "category" in fields and not fields["category"]:
        raise ValueError("种类不能为空")
    with connect() as conn:
        if "category" in fields and fields["category"] not in lists.offered(conn)["categories"]:
            if not add_category:
                raise lists.UnknownNames({"accounts": [], "currencies": [], "pairs": [],
                                          "categories": [fields["category"]]})
            register_accounts(conn, [], [fields["category"]])
        n = conn.execute(
            "UPDATE transactions SET " + ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ? WHERE id = ?",
            (*fields.values(), now(), tx_id)).rowcount
    if not n:
        raise NotFound(tx_id)


def suggestions() -> dict:
    """The lists manual entry offers, in list order (see lists.offered)."""
    with connect() as conn:
        return lists.offered(conn)


def image_file(image_id: int) -> tuple[str, str]:
    """(relative path, original file name) of a stored screenshot."""
    with connect() as conn:
        r = conn.execute("SELECT path, filename FROM images WHERE id = ?", (image_id,)).fetchone()
    if not r:
        raise NotFound(image_id)
    return r["path"], r["filename"]


# ---------- exports ----------

def _csv(rows) -> str:
    def cell(v):
        v = str(v)
        return '"' + v.replace('"', '""') + '"' if re.search(r'[",\n]', v) else v
    lines = [CSV_HEADER] + [
        [r["date"], r["weekday"], r["account"], r["currency"], f"{r['amount']:.2f}", r["category"], r["description"]]
        for r in rows]
    # BOM so Excel reads it as UTF-8
    return "﻿" + "".join(",".join(cell(v) for v in line) + "\r\n" for line in lines)


def create_export(ids: list[int], filename: str) -> str:
    """Record an export of these transactions and return the CSV text."""
    if not ids:
        raise ValueError("没有选择要导出的交易")
    with connect() as conn:
        rows = conn.execute(
            f"SELECT t.* FROM transactions t JOIN ocr_rows r ON r.id = t.ocr_row_id"
            f" WHERE t.id IN ({','.join('?' * len(ids))}) ORDER BY t.date, r.image_id, r.pos DESC",
            ids).fetchall()
        if len(rows) != len(set(ids)):
            raise NotFound("transactions")
        export_id = conn.execute("INSERT INTO exports (at, filename, count) VALUES (?, ?, ?) RETURNING id",
                                 (now(), filename, len(rows))).fetchone()[0]
        conn.executemany("INSERT INTO export_items (export_id, transaction_id) VALUES (?, ?)",
                         [(export_id, r["id"]) for r in rows])
    return _csv(rows)


def list_exports(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, at, filename, count FROM exports ORDER BY id DESC LIMIT ?", (limit,))]
