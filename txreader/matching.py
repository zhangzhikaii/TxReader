"""Decide which freshly recognized rows were already seen in earlier uploads.

Rows are compared on their recognized values (never on edits), only within one source,
and one-to-one: two identical payments on the same day need two earlier rows to match.

Each incoming row gets one verdict:
    skip     identical to a confirmed row -> already in the books
    merge    identical to a pending row -> keep the existing pending row
    suspect  same date and amount but another merchant -> new pending row, flagged
    new      nothing similar -> new pending row
"""


def _dates_match(a: dict, b: dict) -> bool:
    # A guessed year / month may come out differently each time (it depends on the year
    # setting and today's date), so only the part shown on screen counts: day and time.
    if a["date_guessed"] or b["date_guessed"]:
        return a["date"][8:] == b["date"][8:]
    return a["date"] == b["date"]


def _same(a: dict, b: dict) -> bool:
    return (_dates_match(a, b) and a["amount"] == b["amount"]
            and a["merchant"] == b["merchant"] and a["balance"] == b["balance"])


def _similar(a: dict, b: dict) -> bool:
    # Differing balances mean two different payments, whatever the merchant says
    balance_ok = a["balance"] is None or b["balance"] is None or a["balance"] == b["balance"]
    return _dates_match(a, b) and a["amount"] == b["amount"] and balance_ok


def match_rows(incoming: list[dict], existing: list[dict]) -> list[tuple[str, int | None]]:
    """Returns (verdict, id of the matched existing row or None) for each incoming row.

    Rows are dicts with date, amount, merchant, balance, date_guessed; existing rows
    also have id and status. Both lists must already be limited to one source.
    """
    # Prefer rows whose full date is known, so a loose day-only match doesn't steal them
    existing = sorted(existing, key=lambda e: bool(e["date_guessed"]))
    claimed: set[int] = set()
    out: list[tuple[str, int | None] | None] = [None] * len(incoming)

    def claim(row, test):
        for e in existing:
            if e["id"] not in claimed and test(row, e):
                claimed.add(e["id"])
                return e
        return None

    # Exact matches first, so a look-alike can't take a row that is really the same one
    for i, row in enumerate(incoming):
        if e := claim(row, _same):
            out[i] = ("skip" if e["status"] == "confirmed" else "merge", e["id"])
    for i, row in enumerate(incoming):
        if out[i] is None:
            e = claim(row, _similar)
            out[i] = ("suspect", e["id"]) if e else ("new", None)
    return out
