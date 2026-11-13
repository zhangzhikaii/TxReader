"""Every source file is listed in the structure section of its CLAUDE.md, so the docs keep up
with the code: backend modules and tests in the root one, parsers in txreader/parsers/, front-end
scripts in static/."""
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def names(pattern: str) -> list[str]:
    return sorted(p.name for p in ROOT.glob(pattern) if p.name != "__init__.py")


@pytest.mark.parametrize("name", names("txreader/*.py") + names("tests/test_*.py"))
def test_root_lists(name):
    assert name in doc("CLAUDE.md"), f"CLAUDE.md 的结构里没有 {name}"


def test_routes_listed():
    line = next(l for l in doc("CLAUDE.md").splitlines() if l.startswith("txreader/routes/"))
    for name in names("txreader/routes/*.py"):
        assert name.removesuffix(".py") in line, f"CLAUDE.md 的 routes 一行里没有 {name}"


@pytest.mark.parametrize("name", names("txreader/parsers/*.py"))
def test_parsers_listed(name):
    assert name in doc("txreader/parsers/CLAUDE.md"), f"txreader/parsers/CLAUDE.md 里没有 {name}"


@pytest.mark.parametrize("name", names("static/js/*.js") + names("static/*.html") + names("static/*.css"))
def test_static_listed(name):
    assert name in doc("static/CLAUDE.md"), f"static/CLAUDE.md 里没有 {name}"


@pytest.mark.parametrize("name", names("static/js/*.js"))
def test_scripts_loaded(name):
    assert f"js/{name}" in doc("static/index.html"), f"index.html 没有加载 js/{name}"
