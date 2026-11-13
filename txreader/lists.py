"""The account / currency / category lists: what is offered while typing, what counts as new,
and the edits made on the settings page.

Transactions keep the names as text (so the CSV export stays the same); renaming an item
renames it in the transactions too.
"""
import re

from .db import default_base, connect, guess_code, now, transaction
from .errors import NotFound

KINDS = {"accounts": "account", "currencies": "currency", "categories": "category"}  # table -> transactions column
KIND_LABELS = {"accounts": "账户", "currencies": "币种", "categories": "种类"}

# What kind of money an account holds; the balance sheet groups accounts by it, in this order.
# '' (not chosen yet) comes last.
ACCOUNT_TYPES = {"liquid": "活期", "deposit": "定期", "invest": "投资", "credit": "信用卡", "other": "其他"}
UNSET_TYPE = "未分类"


def type_list() -> list[dict]:
    """[{"key", "label"}] in display order, ending with the unset one."""
    return [{"key": k, "label": v} for k, v in ACCOUNT_TYPES.items()] + [{"key": "", "label": UNSET_TYPE}]


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
    each item and why it can't be deleted (`blockers`, empty when it can)."""
    with connect() as conn:
        out = {}
        for table, col in KINDS.items():
            extra = {"accounts": ", type", "currencies": ", is_base, code", "categories": ", fixed"}.get(table, "")
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
        for c in out["categories"]:
            c["fixed"] = bool(c["fixed"])
        for table in KINDS:
            for it in out[table]:
                it["blockers"] = _blockers(conn, table, _get(conn, table, it["id"]))
        out["account_types"] = type_list()
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
            guess_code(conn, item_id, name)
            default_base(conn)
        return item_id


def update(kind: str, item_id: int, changes: dict) -> None:
    """changes: name (renames the transactions too); for categories also fixed (compared month
    by month by default); for accounts also type (an ACCOUNT_TYPES key, or '' for none); for
    currencies also code (ISO 4217, '' for none: rates typed in). A new code drops the rates
    fetched with the old one (all of them for the base currency), so they are fetched again.
    The base currency is fixed (人民币): changing it would make every rate typed in so far wrong."""
    kind = _check_kind(kind)
    with connect() as conn:
        old = _get(conn, kind, item_id)
        if changes.get("fixed") is not None:
            if kind != "categories":
                raise ValueError("只有种类可以设为固定")
            conn.execute("UPDATE categories SET fixed = ? WHERE id = ?", (int(bool(changes["fixed"])), item_id))
        if changes.get("type") is not None:
            if kind != "accounts":
                raise ValueError("只有账户有类型")
            if changes["type"] and changes["type"] not in ACCOUNT_TYPES:
                raise ValueError(f"没有这种账户类型：{changes['type']}")
            conn.execute("UPDATE accounts SET type = ? WHERE id = ?", (changes["type"], item_id))
        if changes.get("code") is not None:
            if kind != "currencies":
                raise ValueError("只有币种有代码")
            code = str(changes["code"]).strip().upper()
            if code and not re.fullmatch(r"[A-Z]{3}", code):
                raise ValueError(f"币种代码是三个字母（ISO 4217，比如 JPY）：{changes['code']}")
            if code and conn.execute("SELECT name FROM currencies WHERE code = ? AND id != ?", (code, item_id)).fetchone():
                raise ValueError(f"已经有币种的代码是 {code} 了")
            if code != old["code"]:
                conn.execute("UPDATE currencies SET code = ? WHERE id = ?", (code, item_id))
                conn.execute("DELETE FROM fx_rates WHERE source != 'manual'"
                             + ("" if old["is_base"] else " AND currency_id = ?"), () if old["is_base"] else (item_id,))
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


def _blockers(conn, kind: str, r) -> list[str]:
    """Why this item can't be deleted (empty: it can). Names are kept as text in transactions,
    email rules and pending rows, so a name still used there would only come back on the next
    import; opening balances, rates and holdings are data that must not go with it."""
    col, name, item_id = KINDS[kind], r["name"], r["id"]
    count = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
    out = []
    if n := count(f"SELECT COUNT(*) FROM transactions WHERE {col} = ?", name):
        out.append(f"有 {n} 笔交易")
    if kind == "currencies" and r["is_base"]:
        out.append("是本位币")
    ref = {"accounts": "account_id", "currencies": "currency_id"}.get(kind)
    if ref and count(f"SELECT COUNT(*) FROM opening_balances WHERE {ref} = ?", item_id):
        out.append("填了期初余额（先清空）")
    if kind == "currencies" and count("SELECT COUNT(*) FROM fx_rates WHERE currency_id = ? AND source = 'manual'", item_id):
        out.append("手填了月汇率（先清空）")  # the fetched ones go with it
    if ref and count(f"SELECT COUNT(*) FROM positions WHERE {ref} = ?", item_id):
        out.append("持仓里用到了")
    if n := count(f"SELECT COUNT(*) FROM email_rules WHERE {col} = ?", name):
        out.append(f"有 {n} 条邮件规则在用（先改规则）")
    if n := count("SELECT COUNT(*) FROM ocr_rows WHERE status = 'pending'"
                  f" AND json_extract(draft, '$.{col}') = ?", name):
        out.append(f"有 {n} 条待核对行在用（先入库或删除）")
    return out


def delete(kind: str, item_id: int) -> None:
    """Only items nothing refers to can be deleted (see _blockers)."""
    kind = _check_kind(kind)
    with connect() as conn:
        r = _get(conn, kind, item_id)
        if why := _blockers(conn, kind, r):
            raise ValueError(f"「{r['name']}」不能删除：{'；'.join(why)}")
        if kind == "currencies":
            conn.execute("UPDATE accounts SET value_currency_id = NULL WHERE value_currency_id = ?", (item_id,))
            conn.execute("DELETE FROM account_currencies WHERE currency_id = ?", (item_id,))
            conn.execute("DELETE FROM fx_rates WHERE currency_id = ?", (item_id,))
        if kind == "accounts":
            conn.execute("DELETE FROM account_currencies WHERE account_id = ?", (item_id,))
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
    {"op": "set_fixed", "kind": "categories", "id", "fixed"}, {"op": "set_type", "kind": "accounts", "id", "type"},
    {"op": "set_code", "kind": "currencies", "id", "code"},
    {"op": "add_currency", "id" (account), "name"} or {"op": "remove_currency", "id", "name"}."""
    actions = {
        "add": lambda o: add(o["kind"], o.get("name")),
        "rename": lambda o: update(o["kind"], o["id"], {"name": o.get("name")}),
        "set_fixed": lambda o: update(o["kind"], o["id"], {"fixed": bool(o.get("fixed"))}),
        "set_type": lambda o: update(o["kind"], o["id"], {"type": o.get("type") or ""}),
        "set_code": lambda o: update(o["kind"], o["id"], {"code": o.get("code") or ""}),
        "delete": lambda o: delete(o["kind"], o["id"]),
        "add_currency": lambda o: add_account_currency(o["id"], o.get("name")),
        "remove_currency": lambda o: remove_account_currency(o["id"], o.get("name")),
    }
    with transaction():
        for o in ops:
            if o.get("op") not in actions:
                raise ValueError(f"不认识的操作：{o.get('op')}")
            actions[o["op"]](o)
