"""The account / currency / category lists: what is offered while typing, what counts as new,
and the edits made on the settings page.

Transactions keep the names as text (so the CSV export stays the same); renaming an item
renames it in the transactions too.
"""
from .db import default_base, connect, now, transaction
from .errors import NotFound

KINDS = {"accounts": "account", "currencies": "currency", "categories": "category"}  # table -> transactions column
KIND_LABELS = {"accounts": "账户", "currencies": "币种", "categories": "种类"}


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"没有这种清单：{kind}")
    return kind


# ---------- reading ----------

def offered(conn) -> dict:
    """What manual entry offers and checks against, in list order:
    {"accounts": [{"name", "currencies": [...]}], "currencies": [...], "categories": [...]}."""
    names = lambda table: [r[0] for r in conn.execute(f"SELECT name FROM {table} ORDER BY sort, id")]
    accounts = [{"name": a, "currencies": []} for a in names("accounts")]
    by_name = {a["name"]: a for a in accounts}
    # An account's currencies: the most used with it first
    for r in conn.execute(
            "SELECT a.name AS account, c.name AS currency FROM account_currencies ac"
            " JOIN accounts a ON a.id = ac.account_id JOIN currencies c ON c.id = ac.currency_id"
            " ORDER BY (SELECT COUNT(*) FROM transactions t WHERE t.account = a.name AND t.currency = c.name) DESC,"
            " c.sort, c.id"):
        by_name[r["account"]]["currencies"].append(r["currency"])
    return {"accounts": accounts, "currencies": names("currencies"), "categories": names("categories")}


def unknown(rows: list[dict], lists: dict) -> dict:
    """What the rows would add to the lists: new accounts, new currencies, known accounts used
    with a currency they haven't had before, and new categories."""
    known = {a["name"]: set(a["currencies"]) for a in lists["accounts"]}
    currencies, categories = set(lists["currencies"]), set(lists["categories"])
    out = {"accounts": [], "currencies": [], "pairs": [], "categories": []}
    for r in rows:
        a, c, cat = r["account"], r["currency"], r["category"]
        if a not in known and a not in out["accounts"]:
            out["accounts"].append(a)
        if c not in currencies and c not in out["currencies"]:
            out["currencies"].append(c)
        if a in known and c not in known[a] and [a, c] not in out["pairs"]:
            out["pairs"].append([a, c])
        if cat not in categories and cat not in out["categories"]:
            out["categories"].append(cat)
    return out


class UnknownNames(ValueError):
    def __init__(self, found: dict):
        self.unknown = found
        parts = [f"新账户 {'、'.join(found['accounts'])}" if found["accounts"] else "",
                 f"新币种 {'、'.join(found['currencies'])}" if found["currencies"] else "",
                 "、".join(f"{a} 的新币种 {c}" for a, c in found["pairs"]),
                 f"新种类 {'、'.join(found['categories'])}" if found["categories"] else ""]
        super().__init__("有还没登记的：" + "；".join(p for p in parts if p) + "。确认要新增后再提交")


def settings() -> dict:
    """Everything the settings page shows: each list in order, with how many transactions use
    each item (an unused one can be deleted)."""
    with connect() as conn:
        out = {}
        for table, col in KINDS.items():
            extra = ", is_base" if table == "currencies" else ""
            out[table] = [dict(r) for r in conn.execute(
                f"SELECT id, name{extra},"
                f" (SELECT COUNT(*) FROM transactions t WHERE t.{col} = x.name) AS used"
                f" FROM {table} x ORDER BY sort, id")]
        pairs = {}
        for r in conn.execute(
                "SELECT ac.account_id, c.name,"
                " (SELECT COUNT(*) FROM transactions t WHERE t.account = a.name AND t.currency = c.name) AS used"
                " FROM account_currencies ac JOIN accounts a ON a.id = ac.account_id"
                " JOIN currencies c ON c.id = ac.currency_id ORDER BY c.sort, c.id"):
            pairs.setdefault(r["account_id"], []).append({"name": r["name"], "used": r["used"]})
        for a in out["accounts"]:
            a["currencies"] = pairs.get(a["id"], [])
        for c in out["currencies"]:
            c["is_base"] = bool(c["is_base"])
        return out


# ---------- settings edits ----------

def _get(conn, kind: str, item_id: int):
    r = conn.execute(f"SELECT * FROM {kind} WHERE id = ?", (item_id,)).fetchone()
    if not r:
        raise NotFound(item_id)
    return r


def _clean_name(name) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    return name


def add(kind: str, name: str) -> int:
    kind = _check_kind(kind)
    name = _clean_name(name)
    with connect() as conn:
        if conn.execute(f"SELECT 1 FROM {kind} WHERE name = ?", (name,)).fetchone():
            raise ValueError(f"已经有{KIND_LABELS[kind]}「{name}」了")
        item_id = conn.execute(
            f"INSERT INTO {kind} (name, sort, created_at)"
            f" VALUES (?, (SELECT COALESCE(MAX(sort), 0) + 1 FROM {kind}), ?) RETURNING id",
            (name, now())).fetchone()[0]
        if kind == "currencies":
            default_base(conn)
        return item_id


def update(kind: str, item_id: int, changes: dict) -> None:
    """changes: name (renames the transactions too). The base currency is fixed (人民币):
    changing it would make every rate typed in so far wrong."""
    kind = _check_kind(kind)
    with connect() as conn:
        old = _get(conn, kind, item_id)
        if changes.get("name") is not None:
            name = _clean_name(changes["name"])
            if name != old["name"]:
                if conn.execute(f"SELECT 1 FROM {kind} WHERE name = ?", (name,)).fetchone():
                    raise ValueError(f"已经有{KIND_LABELS[kind]}「{name}」了，不能改成同名")
                col = KINDS[kind]
                conn.execute(f"UPDATE {kind} SET name = ? WHERE id = ?", (name, item_id))
                conn.execute(f"UPDATE transactions SET {col} = ?, updated_at = ? WHERE {col} = ?",
                             (name, now(), old["name"]))


def reorder(kind: str, ids: list[int]) -> None:
    """Put the items in this order (ids not listed keep their place after them)."""
    kind = _check_kind(kind)
    with connect() as conn:
        rest = [r[0] for r in conn.execute(f"SELECT id FROM {kind} ORDER BY sort, id") if r[0] not in ids]
        for i, item_id in enumerate(list(ids) + rest, 1):
            conn.execute(f"UPDATE {kind} SET sort = ? WHERE id = ?", (i, item_id))


def delete(kind: str, item_id: int) -> None:
    """Only items no transaction uses can be deleted."""
    kind = _check_kind(kind)
    with connect() as conn:
        r = _get(conn, kind, item_id)
        col = KINDS[kind]
        if conn.execute(f"SELECT 1 FROM transactions WHERE {col} = ? LIMIT 1", (r["name"],)).fetchone():
            raise ValueError(f"「{r['name']}」已经有交易在用，不能删除")
        if kind == "currencies" and r["is_base"]:
            raise ValueError("本位币不能删除")
        # Opening balances and rates are data too: don't drop them along with a typo'd name
        ref = {"accounts": "account_id", "currencies": "currency_id"}.get(kind)
        if ref and conn.execute(f"SELECT 1 FROM opening_balances WHERE {ref} = ? LIMIT 1", (item_id,)).fetchone():
            raise ValueError(f"「{r['name']}」填了期初余额，不能删除（先清空期初余额）")
        if kind == "currencies" and conn.execute(
                "SELECT 1 FROM fx_rates WHERE currency_id = ? LIMIT 1", (item_id,)).fetchone():
            raise ValueError(f"「{r['name']}」填了月汇率，不能删除（先清空它的汇率）")
        if kind == "accounts":
            conn.execute("DELETE FROM account_currencies WHERE account_id = ?", (item_id,))
        if kind == "currencies":
            conn.execute("DELETE FROM account_currencies WHERE currency_id = ?", (item_id,))
        conn.execute(f"DELETE FROM {kind} WHERE id = ?", (item_id,))


def add_account_currency(account_id: int, currency: str) -> None:
    currency = _clean_name(currency)
    with connect() as conn:
        _get(conn, "accounts", account_id)
        c = conn.execute("SELECT id FROM currencies WHERE name = ?", (currency,)).fetchone()
        if not c:
            raise ValueError(f"没有币种「{currency}」，先在币种清单里添加")
        conn.execute("INSERT OR IGNORE INTO account_currencies (account_id, currency_id) VALUES (?, ?)",
                     (account_id, c[0]))


def remove_account_currency(account_id: int, currency: str) -> None:
    with connect() as conn:
        a = _get(conn, "accounts", account_id)
        if conn.execute("SELECT 1 FROM transactions WHERE account = ? AND currency = ? LIMIT 1",
                        (a["name"], currency)).fetchone():
            raise ValueError(f"「{a['name']}」已经有{currency}的交易，不能去掉这个币种")
        if conn.execute("SELECT 1 FROM opening_balances WHERE account_id = ?"
                        " AND currency_id = (SELECT id FROM currencies WHERE name = ?)", (account_id, currency)).fetchone():
            raise ValueError(f"「{a['name']}」填了{currency}的期初余额，不能去掉这个币种（先清空期初余额）")
        conn.execute("DELETE FROM account_currencies WHERE account_id = ?"
                     " AND currency_id = (SELECT id FROM currencies WHERE name = ?)", (account_id, currency))


def apply(ops: list[dict]) -> None:
    """The settings page's edits of one list, saved together: all or nothing. Each op is
    {"op": "add", "kind", "name"}, {"op": "rename", "kind", "id", "name"}, {"op": "delete", "kind", "id"},
    {"op": "add_currency", "id" (account), "name"} or {"op": "remove_currency", "id", "name"}."""
    actions = {
        "add": lambda o: add(o["kind"], o.get("name")),
        "rename": lambda o: update(o["kind"], o["id"], {"name": o.get("name")}),
        "delete": lambda o: delete(o["kind"], o["id"]),
        "add_currency": lambda o: add_account_currency(o["id"], o.get("name")),
        "remove_currency": lambda o: remove_account_currency(o["id"], o.get("name")),
    }
    with transaction():
        for o in ops:
            if o.get("op") not in actions:
                raise ValueError(f"不认识的操作：{o.get('op')}")
            actions[o["op"]](o)
