import importlib
import inspect
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
