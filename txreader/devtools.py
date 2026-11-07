"""Developer commands, run against the database the app uses (TXREADER_DATA, also read from .env):

    .venv/bin/python -m txreader.devtools clear              delete every transaction (asks first)
    .venv/bin/python -m txreader.devtools seed <file.csv>    import transactions from seed/<file.csv>

The seed file has the export's columns: 日期,曜日,账户,币种,金额,种类,描述 (曜日 is recomputed,
描述 may be empty). Dates may leave out the time or use '-'; amounts may be written like a
spreadsheet: ¥1,234.00, (¥323.08) for a negative. Accounts, currencies and categories that
aren't on the lists yet are added to them. Seeded rows count as typed in by hand.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
SEED_DIR = ROOT / "seed"
load_dotenv(ROOT / ".env")  # before importing db, so TXREADER_DATA from .env is used

from .db import connect, data_dir, now, register_accounts  # noqa: E402
from .store import TX_REQUIRED, _clean_tx  # noqa: E402

COLUMNS = {"日期": "date", "账户": "account", "币种": "currency", "金额": "amount", "种类": "category",
           "描述": "description"}


def ask(question: str, accept=("y", "yes")) -> bool:
    try:
        return input(question).strip().lower() in accept
    except EOFError:
        return False


def ask_yes(question: str) -> bool:
    """For what can't be undone: only the whole word counts."""
    return ask(question, accept=("yes",))


# ---------- clear ----------

def clear(confirm=ask_yes) -> int:
    db = data_dir() / "txreader.db"
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        exports = conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0]
        confirmed = conn.execute("SELECT COUNT(*) FROM ocr_rows WHERE status = 'confirmed'").fetchone()[0]
    if not n and not exports and not confirmed:
        print(f"No transactions in {db}. Nothing to do.")
        return 0
    print(f"Database: {db}")
    print(f"This will permanently delete {n} transaction(s), {exports} export record(s) and the "
          f"{confirmed} confirmed screenshot row(s) behind them (so those screenshots can be recognized again).")
    print("Kept: pending rows, screenshots, the account / currency / category lists, opening balances, rates.")
    if not confirm("Delete them? Type 'yes' to continue: "):
        print("Cancelled. Nothing was deleted.")
        return 1
    with connect() as conn:
        conn.execute("DELETE FROM export_items")
        conn.execute("DELETE FROM exports")
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM ocr_rows WHERE status = 'confirmed'")
    print(f"Deleted {n} transaction(s).")
    return 0


# ---------- seed ----------

def _amount(text: str) -> float:
    """1,234.00 / ¥1,234 / -5 / (¥323.08) (a negative, as spreadsheets show it)"""
    t = text.strip().replace(",", "").replace("¥", "").replace("￥", "").replace(" ", "")
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    value = float(t)  # ValueError for anything else
    return -value if neg else value


def _date(text: str) -> str:
    """2026/10/29 22:12:00, 2026-10-29 22:12, 2026/10/29 -> 2026/10/29 22:12:00"""
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", text.strip())
    if not m:
        raise ValueError(f"日期格式不对：{text}")
    y, mo, d, h, mi, s = m.groups()
    return f"{y}/{int(mo):02d}/{int(d):02d} {int(h or 0):02d}:{mi or '00'}:{s or '00'}"


def read_seed(path: Path) -> list[dict]:
    """The rows of a seed file, checked; raises ValueError listing every bad line."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader, [])]
        missing = [h for h in COLUMNS if h not in header]
        if missing:
            raise ValueError(f"第一行要是表头，缺少：{'、'.join(missing)}（应为 日期,曜日,账户,币种,金额,种类,描述）")
        idx = {COLUMNS[h]: header.index(h) for h in COLUMNS}
        rows, errors = [], []
        for line, cells in enumerate(reader, 2):
            if not any(c.strip() for c in cells):
                continue  # blank line
            cell = lambda k: cells[idx[k]].strip() if idx[k] < len(cells) else ""
            try:
                raw = {k: cell(k) for k in idx}
                raw["date"] = _date(raw["date"])
                raw["amount"] = _amount(raw["amount"])
                tx = _clean_tx(raw, list(idx))
                if missing := [k for k in TX_REQUIRED if not tx.get(k)]:
                    raise ValueError("缺少 " + "、".join(missing))
                rows.append(tx)
            except ValueError as e:
                errors.append(f"  line {line}: {e}")
    if errors:
        raise ValueError("Some lines can't be imported (nothing was imported):\n" + "\n".join(errors))
    return rows


def seed(name: str, confirm=ask) -> int:
    path = Path(name)
    if not path.is_absolute() and path.parent == Path("."):
        path = SEED_DIR / path
    if not path.exists():
        SEED_DIR.mkdir(exist_ok=True)
        print(f"File not found: {path}\nPut the CSV in {SEED_DIR}/ and pass its name.")
        return 1
    try:
        rows = read_seed(path)
    except ValueError as e:
        print(e)
        return 1
    if not rows:
        print(f"No transactions in {path}.")
        return 1

    key = lambda t: (t["date"], t["account"], t["currency"], t["amount"], t["category"], t.get("description", ""))
    with connect() as conn:
        have = {tuple(r) for r in conn.execute(
            "SELECT date, account, currency, amount, category, description FROM transactions")}
    dupes = sum(key(t) in have for t in rows)
    print(f"Database: {data_dir() / 'txreader.db'}")
    print(f"{path.name}: {len(rows)} transaction(s), {min(t['date'] for t in rows)[:10]} to {max(t['date'] for t in rows)[:10]}")
    if dupes and not confirm(f"{dupes} of them are already in the database (same date, account, currency, "
                             f"amount, category and description). Import anyway? [y/N] "):
        print("Cancelled. Nothing was imported.")
        return 1

    with connect() as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("accounts", "currencies", "account_currencies", "categories")}
        register_accounts(conn, [(t["account"], t["currency"]) for t in rows], [t["category"] for t in rows])
        t0 = now()
        conn.executemany(
            "INSERT INTO transactions (origin, date, weekday, account, currency, amount, category, description,"
            " confirmed_at, updated_at) VALUES ('manual', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(t["date"], t["weekday"], t["account"], t["currency"], t["amount"], t["category"],
              t.get("description", ""), t0, t0) for t in rows])
        added = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] - n for t, n in before.items()}
    print(f"Imported {len(rows)} transaction(s). Added to the lists: {added['accounts']} account(s), "
          f"{added['currencies']} currency(ies), {added['account_currencies']} account-currency pair(s), "
          f"{added['categories']} category(ies).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m txreader.devtools", description="TxReader developer commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clear", help="delete every transaction (asks first)")
    p = sub.add_parser("seed", help=f"import transactions from a CSV in {SEED_DIR.name}/")
    p.add_argument("file", help="file name in seed/ (or a path)")
    args = parser.parse_args(argv)
    return clear() if args.command == "clear" else seed(args.file)


if __name__ == "__main__":
    sys.exit(main())
