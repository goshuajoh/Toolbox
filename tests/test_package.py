import importlib
import inspect
import json
from pathlib import Path

import toolbank

ROOT = Path(__file__).resolve().parents[1]


def _public(mod):
    return {n: o for n, o in vars(mod).items() if not n.startswith("_") and
            (inspect.isfunction(o) or inspect.isclass(o)) and o.__module__ == mod.__name__}


def test_every_module_registered():
    files = {p.stem for p in (ROOT / "toolbank").glob("*.py") if p.stem != "__init__"}
    assert files == set(toolbank._MODULES)


def test_no_duplicate_names_across_modules():
    seen = {}
    for m in toolbank._MODULES:
        for name in _public(importlib.import_module(f"toolbank.{m}")):
            assert name not in seen, f"{name} defined in both {seen[name]} and {m}"
            seen[name] = m
    assert set(seen) <= set(toolbank.__all__)


def test_every_public_function_has_a_docstring():
    missing = [f"{m}.{n}" for m in toolbank._MODULES for n, o in _public(importlib.import_module(f"toolbank.{m}")).items()
               if not (o.__doc__ or "").strip()]
    assert missing == []


def test_star_import_exports_functions_not_modules():
    ns = {}
    exec("from toolbank import *", ns)
    assert callable(ns["excel_app"]) and callable(ns["read_sheet"]) and "PATTERNS" in ns and "ROUND_HALF_UP" not in ns


def test_index_generator_covers_every_module():
    import runpy
    text = runpy.run_path(str(ROOT / "make_index.py"), run_name="not_main")["build"]()
    for m in toolbank._MODULES:
        assert f"`toolbank/{m}.py`" in text
    assert "| `  .capture_json()` |" in text and "| `  .process()` |" in text


def test_docs_site_in_sync():
    import runpy

    import pytest
    pytest.importorskip("markdown")              # build-time only: renders METHODOLOGY.md for the site
    m = runpy.run_path(str(ROOT / "make_index.py"), run_name="not_main")
    assert m["main"](["--check"]) == 0, "README index or docs/ out of date: run python make_index.py"
    data = json.loads((ROOT / "docs" / "toolbox.json").read_text(encoding="utf-8"))
    n = sum(len(_public(importlib.import_module(f"toolbank.{x}"))) for x in toolbank._MODULES)
    assert data["version"] == toolbank.__version__ and data["counts"]["functions"] == n
    assert [x["name"] for x in data["modules"]] == toolbank._MODULES and len(data["needs"]) > 20
    lookup = next(f for mod in data["modules"] for f in mod["functions"] if f["name"] == "html_select")
    assert lookup["needs"] == ["beautifulsoup4"] and lookup["params"][0] == {"name": "html", "kind": "pos", "default": None}
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "__TOOLBOX_DATA__" not in html and "__REPO__" not in html and f"v{toolbank.__version__}" in html
