"""The developer commands, on a throwaway data directory."""
import pytest

from txreader import devtools
from txreader.db import connect

SEED = ("\ufeff日期,曜日,账户,币种,金额,种类,描述\n"
        '2026/10/01 12:00:00,木,微信,人民币,-10.00,饮食费,"Shop, ""A"""\n'
        '2026-10-02,,现金,日元,"(¥1,500)",饮食费,\n'
        '2026/10/03 09:30,,现金,人民币,¥200.5,红包,\n')


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(devtools, "SEED_DIR", tmp_path / "seed")
    (tmp_path / "seed").mkdir()
    return tmp_path


def rows():
    with connect() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT date, weekday, account, currency, amount, category, description, origin FROM transactions ORDER BY id")]


def test_seed_imports_and_registers(data, capsys):
    (data / "seed" / "oct.csv").write_text(SEED, encoding="utf-8")
    assert devtools.seed("oct.csv", confirm=lambda q: pytest.fail("shouldn't ask")) == 0
    assert rows() == [
        ("2026/10/01 12:00:00", "木", "微信", "人民币", -10.0, "饮食费", 'Shop, "A"', "manual"),
        ("2026/10/02 00:00:00", "金", "现金", "日元", -1500.0, "饮食费", "", "manual"),
        ("2026/10/03 09:30:00", "土", "现金", "人民币", 200.5, "红包", "", "manual"),
    ]
    with connect() as conn:
        assert [r[0] for r in conn.execute("SELECT name FROM accounts ORDER BY sort")] == ["微信", "现金"]
        assert [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY sort")] == ["饮食费", "红包"]
        assert conn.execute("SELECT COUNT(*) FROM account_currencies").fetchone()[0] == 3
    assert "Imported 3 transaction(s)" in capsys.readouterr().out

    # The same file again: asks, and does nothing when declined
    assert devtools.seed("oct.csv", confirm=lambda q: False) == 1
    assert len(rows()) == 3


def test_seed_rejects_bad_files(data, capsys):
    bad = SEED + "2026/02/30,,现金,日元,-1,饮食费,\n2026/10/04,,现金,日元,abc,饮食费,\n2026/10/05,,,日元,-1,饮食费,\n"
    (data / "seed" / "bad.csv").write_text(bad, encoding="utf-8")
    assert devtools.seed("bad.csv") == 1
    out = capsys.readouterr().out
    assert "line 5" in out and "line 6" in out and "line 7" in out
    assert rows() == []  # all or nothing

    (data / "seed" / "nohead.csv").write_text("2026/10/01,木,微信,人民币,-1,饮食费,\n", encoding="utf-8")
    assert devtools.seed("nohead.csv") == 1
    assert devtools.seed("missing.csv") == 1


def test_clear_asks_first(data, capsys):
    (data / "seed" / "oct.csv").write_text(SEED, encoding="utf-8")
    devtools.seed("oct.csv")

    assert devtools.clear(confirm=lambda q: False) == 1
    assert len(rows()) == 3
    asked = []
    assert devtools.clear(confirm=lambda q: asked.append(q) or True) == 0
    assert "Type 'yes'" in asked[0]
    assert rows() == []
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2  # the lists stay
    assert devtools.clear(confirm=lambda q: pytest.fail("nothing to ask")) == 0
