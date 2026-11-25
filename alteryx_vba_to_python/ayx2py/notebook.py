"""A Jupyter notebook that runs the generated module one container at a time, on the user's files.

Cells: settings (the file every Input step reads, pre-filled with the workflow's own paths), setup,
one cell per container (one line per step: rows and columns out, or why it failed; a failing step
skips only the steps that need it), the steps that failed, look() at any step, and compare() of
each Output step with the file the legacy workflow wrote. Standard library only (nbformat 4 JSON).
"""
import json
import os

from .codegen import file_links
from .guide import Guide, designer, place


def _code(lines):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": [x + "\n" for x in lines[:-1]] + lines[-1:]}


def _md(lines):
    return {"cell_type": "markdown", "metadata": {}, "source": [x + "\n" for x in lines[:-1]] + lines[-1:]}


def _raw(path):
    """A Python raw-string literal for a path (a raw string cannot end in a backslash)."""
    return 'r"%s"' % path if not path.endswith("\\") and '"' not in path else repr(path)


def _comment_line(code, comment, width=58):
    return "%-*s  # %s" % (width, code, comment)


def build(wf, module, workflow_dir):
    """-> notebook JSON text. module: name of the generated module; workflow_dir: where the workflow's relative paths start."""
    g = Guide(wf)
    t = wf.tools
    live = [k for k in g.tops if any(g.top_of[i] == k for i in g.order)]
    where = lambda i: "%s %s · container %d. %s" % (g.sid[i], designer(t[i].kind), g.cnum[g.top_of[i]], g.cname(g.top_of[i]))
    links = file_links(wf, g)

    inputs, later = [], []
    for i in g.order:
        k = t[i].kind
        if k == "Input":
            note = where(i) + ("; written by step %s in this run (CHAIN)" % g.sid[links[i]] if i in links else "")
            inputs.append("    " + _comment_line("%r: %s," % (i, _raw(t[i].ir["file"])), note))
        elif k == "DynamicInput":
            inputs.append("    # " + _comment_line("%r: %s," % (i, _raw(t[i].ir["file"])), where(i) +
                                                   ": reads the files named in [%s]; uncomment to read this one file" % t[i].ir["field"]))
        elif k == "Directory":
            later.append("# INPUTS[%r] = pd.DataFrame({\"FullPath\": [...], \"FileName\": [...]})   # %s: lists %s\\%s" % (
                i, where(i), t[i].ir["dir"], t[i].ir["spec"]))
    stubs = ["    # " + _comment_line("%r: lambda *tables: tables[0]," % i, where(i) + ": not converted; " +
                                     "; ".join(t[i].reasons()))
             for i in g.order if t[i].status == "stub"]
    settings = [
        "# Settings: edit these, then run the notebook from the top (Run All).",
        "import pandas as pd",
        "",
        "WORKFLOW_DIR = %s   # the workflow's relative paths start here" % _raw(workflow_dir),
        'CODE_DIR = ""        # folder holding %s.py and ayx_runtime.py; "" = this notebook\'s folder' % module,
        "WRITE = False        # False: Output steps keep their rows in memory (ctx.outputs) and write no file",
        "OUTPUT_DIR = None    # with WRITE = True: write the files into this folder instead of the workflow's paths",
        "CHAIN = True         # an Input that reads a file an earlier Output step writes gets that step's rows",
        "",
        "# The file each Input step reads, as the workflow has it. Change a path to your own copy",
        "# (for Excel: book.xlsx|||Sheet1$ or book.xlsx|||Sheet1$A1:F200), or give a DataFrame.",
        "INPUTS = {"] + (inputs or ["    # this workflow has no Input step"]) + ["}"] + later + [
        "",
        "# A step that is not converted stops with NotConverted; give a function to stand in for it.",
        "# It gets the step's input tables and returns its output table.",
        "STUBS = {"] + (stubs or ["    # every step is converted"]) + ["}"]

    setup = [
        "import importlib, os, sys",
        "sys.path.insert(0, os.path.abspath(CODE_DIR or \".\"))",
        "import ayx_runtime as rt",
        "importlib.reload(rt)",
        "wf = importlib.reload(importlib.import_module(%r))" % module,
        "",
        "ctx = rt.Context(workflow_dir=WORKFLOW_DIR, inputs=INPUTS, write=WRITE, out_dir=OUTPUT_DIR, stubs=STUBS)",
        "s = {}   # every step's output so far: s[tool id]",
        "",
        "def look(step, n=10, output=None):",
        '    """A step\'s rows and columns; returns its first n rows. step: "2.16" (step number) or "44" (tool ID).',
        '    output: "True" / "False" for a Filter, "Join" / "Left" / "Right" for a Join."""',
        "    return rt.look(s, step, n, output, wf.STEPS, ctx)",
        "",
        "def compare(step, legacy, sheet=None, key=None, tol=1e-6):",
        '    """An Output step\'s rows against the file the legacy workflow wrote: row counts, columns, differing cells.',
        "    Text ignores case, null and \"\" are alike, numbers may differ by tol. key=[columns]: line rows up by",
        '    these columns instead of by position. Returns the differing cells as a table."""',
        "    path = ctx.path(legacy) if isinstance(legacy, str) else legacy",
        "    return rt.compare(rt.step_output(s, step, wf.STEPS, ctx), path, sheet, key, tol)",
        "",
        "for k, name in wf.CONTAINERS.items():",
        "    steps = [st for st in wf.STEPS if st[1] == k]",
        '    print("%d. %s: %d steps (%s to %s)" % (k, name, len(steps), steps[0][2], steps[-1][2]))']

    cells = [_md(["# Test %s step by step" % os.path.basename(wf.source), "",
                  "1. In **Settings**, point each Input step at a copy of its source file.",
                  "2. Run the notebook from the top. Each container's cell prints one line per step: rows and "
                  "columns out, or why it failed. A failing step skips only the steps that need its rows; the rest "
                  "still run.",
                  "3. `look(\"2.16\")` shows any step's first rows and its columns (step numbers are the migration "
                  "guide's).",
                  "4. `compare(...)` checks an Output step against the file the Alteryx workflow wrote.",
                  "",
                  "Nothing is written while `WRITE = False`. Re-run a container's cell after changing a setting; it "
                  "re-runs that container only."]),
             _code(settings), _code(setup)]
    for key in g.tops:
        n = g.cnum[key]
        tools = [i for i in g.order if g.top_of[i] == key]
        if key not in live:
            cells.append(_md(["## %d. %s" % (n, g.cname(key)), "", "Disabled in the workflow: nothing to run."]))
            continue
        files = []
        for i in tools:
            k = t[i].kind
            if k in ("Input", "Output", "DynamicInput"):
                path, sheet, cells_, _ = place(t[i].ir["file"], t[i].ir["options"])
                verb = {"Input": "reads", "Output": "writes", "DynamicInput": "reads files like"}[k]
                files.append("- step %s %s `%s`%s%s" % (g.sid[i], verb, path, ", sheet %s" % sheet if sheet else "",
                                                       ", cells %s" % cells_.upper() if cells_ else ""))
            elif k == "Directory":
                files.append("- step %s lists `%s` (`%s`)" % (g.sid[i], t[i].ir["dir"], t[i].ir["spec"]))
        cells.append(_md(["## %d. %s" % (n, g.cname(key)), "",
                          "Steps %s to %s (%d tools)." % (g.sid[tools[0]], g.sid[tools[-1]], len(tools))] +
                         ([""] + files if files else [])))
        cells.append(_code(["s = wf.run(ctx, only=%d, s=s, keep_going=True, report=True, chain=CHAIN)" % n]))

    outs = [i for i in g.order if t[i].kind == "Output"]
    first = outs[0] if outs else g.order[0]
    shown = next((e[0] for e in wf.ins.get(first, [])), first) if outs else first
    cells += [_md(["## Steps without an output", "", "Every step that failed, and the steps skipped because they "
                   "need its rows."]),
              _code(["rt.problems(ctx, wf.STEPS)"]),
              _md(["## Look at a step", "", "Any step number from the guide (or a tool ID). For a Filter, "
                   "`look(\"2.2\", output=\"False\")` shows the rows that failed the test."]),
              _code(['look("%s")' % g.sid[shown]]),
              _md(["## Compare with the legacy output", "",
                   "Each line compares one Output step with a file the Alteryx workflow wrote. The paths are the "
                   "workflow's own; point them at a copy of the legacy output. Add `key=[\"ID\"]` to line rows "
                   "up by key columns instead of by position."]),
              _code(["%s" % _comment_line('diff_%s = compare("%s", %s%s)' % (
                  g.sid[i].replace(".", "_"), g.sid[i], _raw(place(t[i].ir["file"])[0]),
                  ", %r" % place(t[i].ir["file"])[1] if place(t[i].ir["file"])[1] else ""), where(i), 70)
                     for i in outs] or ["# this workflow has no Output step"])]
    nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                       "language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    for n, c in enumerate(nb["cells"]):
        c["id"] = "c%02d" % n
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
