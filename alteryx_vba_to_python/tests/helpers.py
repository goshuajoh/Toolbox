"""Shared test helpers: build a synthetic workflow, convert it, import the generated module."""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "ayx2py"), os.path.join(ROOT, "vba2py"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import ayx_runtime as rt                                    # noqa: E402  (top-level, as generated code imports it)
from ayx2py import codegen, expr, spec, workflow            # noqa: E402

_n = [0]


def evaluate(text, row=None, **extra):
    """Translate one Alteryx expression and run it on one row."""
    ns = {"rt": rt}
    exec("\n".join(expr.to_function(expr.parse(text), "f", "r, cur=None, cur_name=None, cur_type=None")), ns)
    return ns["f"](rt.Row(row or {}), **extra)


def convert(wb, tmp=None):
    """WB -> (Workflow, generated module, spec lines)."""
    tmp = tmp or tempfile.mkdtemp(prefix="ayx2py_test_")
    path = os.path.join(tmp, "wf.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    wf = workflow.Workflow(path)
    code, _ = codegen.generate(wf)
    _n[0] += 1
    mod_path = os.path.join(tmp, "generated_%d.py" % _n[0])
    with open(mod_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(code) + "\n")
    s = importlib.util.spec_from_file_location("generated_%d" % _n[0], mod_path)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    lines, info = spec.build(wf)
    return wf, mod, lines, info


def table(df):
    return [tuple(r.values()) for r in rt.rows_of(df)]
