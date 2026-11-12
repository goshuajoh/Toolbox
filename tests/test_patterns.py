"""Every block in patterns/ follows the format, imports only real toolbank names, loads without optional
libraries, and its demo(tmp) runs offline."""
import runpy
from pathlib import Path

import pytest

import toolbank

ROOT = Path(__file__).resolve().parents[1]
MI = runpy.run_path(str(ROOT / "make_index.py"), run_name="not_main")
FILES = sorted((ROOT / "patterns").glob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_pattern(path, tmp_path, monkeypatch):
    meta = MI["pattern_meta"](path)
    assert set(meta["blocks"]) <= set(toolbank.__all__), "blocks must be toolbank names"
    assert meta["decisions"] and meta["governance"] and len(meta["title"]) <= 70
    monkeypatch.chdir(tmp_path)
    ns = runpy.run_path(str(path), run_name="pattern")
    if meta["demo"]:
        ns["demo"](tmp_path)


def test_patterns_unique_and_ordered():
    metas = MI["patterns"]()
    assert len({m["order"] for m in metas}) == len(metas) and len({m["title"] for m in metas}) == len(metas)
