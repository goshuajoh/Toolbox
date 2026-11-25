"""Two Jupyter notebooks, written as nbformat 4 JSON with the standard library only.

build_steps: the default. A plain pandas template to rebuild the workflow by hand, branch by branch: settings
(the workflow's folder and one variable per file it reads), the guide's two helpers, a Load cell that reads every
file once, then one skeleton cell per branch that lists its steps as comments, ready for the pandas lines pasted
from the migration guide, and a last cell that saves the outputs and compares one with the legacy file.

build_runner (--runner-notebook): runs the generated module one container at a time, on the user's files.
Cells: settings (the file every Input step reads, pre-filled with the workflow's own paths), setup,
one cell per container (one line per step: rows and columns out, or why it failed; a failing step
skips only the steps that need it), the steps that failed, look() at any step, and compare() of
each Output step with the file the legacy workflow wrote. Standard library only (nbformat 4 JSON).
"""
import json
import os
import re

from .codegen import file_links
from .guide import HELPERS, MULTI, Guide, _bkey, designer, file_text, path_parts, place


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


def _notebook(cells):
    nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                       "language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    for n, c in enumerate(nb["cells"]):
        c["id"] = "c%02d" % n
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def _plain(text):
    """Guide text for a notebook: links become their label."""
    return re.sub(r"\[\[[\w-]+\|([^\]]+)\]\]", r"\1", text)


def _cell(text):
    return _plain(text).replace("|", "\\|")


def build_steps(wf, workflow_dir, guide=None):
    """-> the plain pandas template (JSON text). workflow_dir: the folder the workflow's relative paths start from."""
    g = guide or Guide(wf)
    L, t = g.L, wf.tools
    cells = [_md(["# Rebuild %s in pandas, step by step" % os.path.basename(wf.source), "",
                  "1. **Settings**: set `FOLDER` to the workflow's folder, and change any file path to your own copy.",
                  "2. Run **Helpers** and **Load**: every file the workflow opens is read once, into a `src_…` table.",
                  "3. Then go branch by branch, as in the migration guide. Each code cell starts from the table the "
                  "branch receives and lists its steps as comments. Under each comment, paste that step's pandas "
                  "lines from the guide (the folded line under the branch's table), run the cell, and compare "
                  "`df.head()` with what Alteryx shows for that tool.",
                  "4. The last cell saves the outputs and compares one with the file the Alteryx workflow saved.",
                  "",
                  "Step numbers (2.16) and table names (`s2_16`, `src_…`) are the guide's, so a pasted line finds its "
                  "inputs."])]

    def users(i):
        if i in g.fold_of:
            return "%s (inside)" % g.sid[g.folded[g.fold_of[i]][0]]
        return g.sid[i]

    settings = ["# Settings: the workflow's folder and the files it opens. Change a path to your own copy.",
                "import os", "",
                "FOLDER = %s   # the workflow's relative paths start here" % _raw(workflow_dir),
                'OUT = os.path.join(FOLDER, "python_out")   # where the last cell saves the outputs', ""]
    for var, path, tools in g.paths.values():
        settings.append("%s = %s   # %s; opened at step %s" % (var, path_parts(path), path,
                                                             ", ".join(dict.fromkeys(users(i) for i in tools))))
    if not g.paths:
        settings.append("# this workflow opens no file directly")
    cells += [_code(settings),
              _md(["## Helpers", "", "Alteryx compares text ignoring case, and treats null and the empty string as "
                   "different; the guide's pandas lines use these two helpers."]),
              _code(list(HELPERS))]

    load = []
    for read in g.reads.values():
        first = read["tools"][0]
        what = "a typed table" if t[first].kind == "TextInput" else \
            _plain(file_text(t[first].ir["file"], t[first].ir["options"])).replace("`", "")
        load += ["# step %s: %s" % (", ".join(dict.fromkeys(users(i) for i in read["tools"])), what)]
        load += g.load_lines(first)
    load += [""] + ['print("%s", %s.shape)' % (r["var"], r["var"]) for r in g.reads.values()]
    cells += [_md(["## Load", "", "Every file the workflow opens, read once. In Alteryx each is its own Input tool; the "
                   "guide shows a file that only feeds a Join, Find Replace or Union inside that step."]),
              _code(load if g.reads else ["# this workflow opens no file directly"])]

    # branches, in an order where every table a cell starts from is made by an earlier cell
    stages = sorted(g.bid, key=lambda s: (g.cnum[g.top_of[L.stages[s][0]]], _bkey(g.bid[s])))

    def needs(s):
        ids = set(L.stages[s])
        return {L.stage_of[e[0]] for i in L.stages[s] for e in wf.ins.get(i, []) if e[0] not in ids and e[0] not in g.fold_of}

    made, pending, ordered = set(), list(stages), []
    while pending:
        nxt = next((s for s in pending if needs(s) <= made), pending[0])
        pending.remove(nxt)
        ordered.append(nxt)
        made.add(nxt)

    shown_key, seen_keys, shown_group = None, set(), set()
    for s in ordered:
        ids = L.stages[s]
        key = g.top_of[ids[0]]
        if key != shown_key:
            files = []
            for i in g.order:
                if g.top_of[i] == key and t[i].kind in ("Input", "Output", "DynamicInput"):
                    verb = {"Input": "opens", "Output": "saves", "DynamicInput": "opens each file like"}[t[i].kind]
                    files.append("- step %s %s %s" % (users(i), verb, file_text(t[i].ir["file"], t[i].ir["options"])))
            again = key in seen_keys
            cells.append(_md(["## %d. %s%s" % (g.cnum[key], g.cname(key), " (continued)" if again else "")] +
                             ([""] + files if files and not again else [])))
            shown_key = key
            seen_keys.add(key)
        copy = L.copy_of.get(s)
        if copy and copy not in shown_group:
            shown_group.add(copy)
            group = [x for x in stages if L.copy_of.get(x) == copy]
            tools = [i for x in group for i in L.stages[x]]
            span = "%s–%s" % (g.sid[min(tools, key=lambda i: g.pos[i])], g.sid[max(tools, key=lambda i: g.pos[i])].split(".")[1])
            cells.append(_md(["### Branches %s–%s: a repeat of %s" % (g.bid[group[0]], g.bid[group[-1]].split(".")[1],
                                                                    g.span_text([g.twin_stage(x) for x in group], True)),
                              "", "The same steps and rules as there, on other tables. Paste from the guide's folded line "
                              "\"Steps %s in pandas\" under this repeat." % span]))
        elif not copy:
            rows = ["| Step | Tool | What it does |", "|---|---|---|"]
            for i in ids:
                rows.append("| %s | %s | %s |" % (g.sid[i], _cell(designer(t[i].kind) + (" “%s”" % t[i].note if t[i].note else "")),
                                                   _cell(g.short(i))))
            cells.append(_md(["### Branch %s — %s" % (g.bid[s], _plain(g.label(s))), ""] + rows))
        cells.append(_code(skeleton(g, s)))

    outs = [i for i in g.order if t[i].kind == "Output"]
    last = ["# Save what each Output step saves (uncomment to write the files into OUT):",
            "# os.makedirs(OUT, exist_ok=True)"]
    for i in outs:
        path, sheet, _, _ = place(t[i].ir["file"])
        name = os.path.basename(path.replace("\\", "/"))
        last.append("# %s.%s" % (g.var(i), 'to_excel(os.path.join(OUT, "%s"), sheet_name="%s", index=False)' % (name, sheet or "Sheet1")
                                  if name.lower().endswith((".xlsx", ".xlsm", ".xls")) else
                                  'to_csv(os.path.join(OUT, "%s"), index=False)' % name))
    if outs:
        i = outs[-1]
        path, sheet, _, _ = place(t[i].ir["file"])
        v = g.var(i)
        excel = path.lower().endswith((".xlsx", ".xlsm", ".xls"))
        last += ["", "# Compare step %s's rows with the file the Alteryx workflow saved (point this at a copy of it):" % g.sid[i],
                 "legacy_file = %s" % path_parts(path),
                 "if os.path.exists(legacy_file):",
                 "    legacy = %s" % ('pd.read_excel(legacy_file, sheet_name="%s")' % (sheet or "Sheet1") if excel else
                                      "pd.read_csv(legacy_file, dtype=str)"),
                 '    print("rows:", len(%s), "here,", len(legacy), "in the legacy file; columns only here:", '
                 'sorted(set(%s.columns) - set(legacy.columns)), "only there:", sorted(set(legacy.columns) - set(%s.columns)))'
                 % (v, v, v),
                 "    common = [c for c in %s.columns if c in legacy.columns]" % v,
                 '    print(%s[common].reset_index(drop=True).compare(legacy[common]) if len(%s) == len(legacy) else '
                 '"the row counts differ")' % (v, v)]
    cells += [_md(["## Save and compare"]), _code(last if outs else ["# this workflow saves no file"])]
    return _notebook(cells)


def skeleton(g, s):
    """The code cell for one branch: the table it starts from, one comment per step to paste under, its output."""
    L, wf, t = g.L, g.wf, g.t
    ids = L.stages[s]
    f = ids[0]
    lines = ["# Branch %s — %s" % (g.bid[s], _plain(g.label(s)))]
    ins = wf.ins.get(f, [])
    start = None
    if t[f].kind in ("Input", "TextInput"):
        start = g.src_of.get(f)
    elif t[f].kind not in ("Union", "Directory") and ins:
        pick = {"Join": "L", "FindReplace": "T", "AppendFields": "T"}.get(t[f].kind)
        e = next((e for e in ins if pick and e[3][:1].upper() == pick), None) or g.L.primary(f) or ins[0]
        start = g.var(e[0], e[1])
    if start:
        lines.append("df = %s" % start)
    for i in ids:
        note = "already loaded as %s" % g.src_of[i] if i in g.src_of else "paste the pandas lines from the guide"
        lines += ["# %s %s — %s   (%s)" % (g.sid[i], designer(t[i].kind) + (" “%s”" % t[i].note if t[i].note else ""),
                                          _plain(g.short(i)).replace("**", ""), note), ""]
    last = ids[-1]
    used = sorted({e[1] for e in wf.outs.get(last, [])})
    if t[last].kind in MULTI:
        names = [g.var(last, a) for a in used] or ["df"]
        lines += ["# step %s's lines make %s" % (g.sid[last], ", ".join(names)), "%s.head()" % names[0]]
    else:
        lines += ["%s = df" % g.var(last), "df.head()"]
    return lines


def build_runner(wf, module, workflow_dir):
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
    return _notebook(cells)
