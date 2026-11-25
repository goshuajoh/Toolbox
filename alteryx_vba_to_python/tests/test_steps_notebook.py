"""The plain pandas notebook: filled with the pandas lines the migration guide shows (parsed from its Markdown,
as a person would copy them) and run top to bottom on files the test creates."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import openpyxl

from helpers import ROOT
import synth_recon
from ayx2py import __main__ as cli
from ayx2py import guide, workflow

sys.path.insert(0, os.path.join(ROOT, "examples"))
import make_examples  # noqa: E402

RUNNER = r"""
import json, sys
nb = json.load(open(sys.argv[1], encoding="utf-8"))
ns = {"__name__": "__main__"}
for n, cell in enumerate(nb["cells"]):
    if cell["cell_type"] == "code":
        print("=== cell %d" % n)
        exec(compile("".join(cell["source"]), "cell%d" % n, "exec"), ns)
print("=== done")
"""


def guide_lines(md):
    """{step: [pandas lines]} as the guide's folded lines show them."""
    out, sid, block, lines = {}, None, False, md.splitlines()
    for line in lines + ["- end"]:
        m = re.match(r"- \*\*(\d+\.\d+[a-z]*|Steps \S+)\*\* ", line)
        if m or line.startswith("- "):
            if sid:
                out.update(take(sid, block))
            sid, block = (m.group(1), []) if m else (None, False)
            if not m:
                continue
            spans = re.findall(r"`([^`]*)`", line)
            if spans and not re.fullmatch(r"t\w+", spans[-1]) and not line.startswith("- **Steps "):
                block = ["# pandas:", spans[-1]]
        elif sid is not None and block is not False and (line.startswith("      ") or not line.strip()):
            if line.strip():
                block.append(line[6:])
        elif line.strip():
            if sid:
                out.update(take(sid, block))
            sid, block = None, False
    return out


def take(sid, block):
    if any(re.match(r"# step \S+ ", x) for x in block):           # a repeat: lines for each of its steps
        out, cur = {}, None
        for x in block:
            m = re.match(r"# step (\S+) ", x)
            if m:
                cur = out.setdefault(m.group(1), [])
            elif cur is not None:
                cur.append(x)
        return out
    if "# pandas:" in block:
        block = block[block.index("# pandas:") + 1:]
    return {sid: block}


def fill(nb_path, md):
    lines = guide_lines(md)
    with open(nb_path, encoding="utf-8") as fh:
        nb = json.load(fh)
    pasted = 0
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        out = []
        for line in "".join(cell["source"]).split("\n"):
            out.append(line)
            m = re.match(r"# (\S+) .*\(paste the pandas lines from the guide\)$", line)
            if m:
                out += lines[m.group(1)]
                pasted += 1
        cell["source"] = "\n".join(out)
    with open(nb_path, "w", encoding="utf-8") as fh:
        json.dump(nb, fh)
    return pasted


def run(nb_path):
    p = subprocess.run([sys.executable, "-c", RUNNER, nb_path], cwd=os.path.dirname(nb_path), capture_output=True,
                       text=True, encoding="utf-8")
    return p.returncode, p.stdout + p.stderr


def generate(folder, wb, name):
    path = os.path.join(folder, name + ".yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    out = os.path.join(folder, "out")
    with open(os.devnull, "w") as quiet:
        old, sys.stdout = sys.stdout, quiet
        try:
            cli.main([path, out, "--no-code"])
        finally:
            sys.stdout = old
    with open(os.path.join(out, name + "_guide.md"), encoding="utf-8") as fh:
        md = fh.read()
    return os.path.join(out, name + "_steps.ipynb"), md, path


def xlsx(path, sheet, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


class DatasetAudit(unittest.TestCase):
    def test_filled_with_the_guides_lines_it_runs_top_to_bottom(self):
        d = tempfile.mkdtemp(prefix="steps_")
        from test_notebook import COLS, ROWS, make_files
        make_files(d)
        xlsx(os.path.join(d, "Output", "Dataset audit.xlsx"), "Issues", ["Name", "Owner", "Issue"], [["a", "b", "c"]])
        nb, md, _ = generate(d, make_examples.dataset_audit(), "dataset_audit")
        with open(nb, encoding="utf-8") as fh:
            cells = json.load(fh)["cells"]
        code = ["".join(c["source"]) for c in cells if c["cell_type"] == "code"]
        self.assertTrue('DATASETS_EXPORT_CSV = os.path.join(FOLDER, "Input", "datasets_export.csv")' in code[0])
        self.assertTrue(code[2].startswith("# step 1.1: datasets_export.csv\nsrc_datasets_export = pd.read_csv(DATASETS_EXPORT_CSV"))
        self.assertTrue("src_typed_owner_team = pd.DataFrame([['ana', 'Finance'], ['bo', 'Risk']]" in code[2])
        tags = next(c for c in code if c.startswith("# Branch 2.4 "))
        self.assertEqual(tags.split("\n")[1], "df = s2_6")
        self.assertTrue("# 2.15 Join — matches its rows with a typed table ([Owner], [Team]; 2 rows) on [Owner]   (paste" in tags)
        self.assertTrue(tags.endswith("# step 2.15's lines make s2_15_join\ns2_15_join.head()"))
        self.assertFalse(any("# 2.15a" in c for c in code))                 # the typed table is not a step of its own
        self.assertTrue("# s2_27.to_excel(os.path.join(OUT, \"Dataset audit.xlsx\"), sheet_name=\"Issues\", index=False)" in code[-1])
        self.assertEqual(fill(nb, md), 30)                   # every step but the one the Load cell reads
        rc, log = run(nb)
        self.assertEqual(rc, 0, log)
        self.assertTrue("=== done" in log and "src_datasets_export (3, 9)" in log, log)
        self.assertTrue("rows: 11 here, 1 in the legacy file" in log, log)


def used_columns(g):
    """Every column name the workflow refers to, for test files that carry them all."""
    names, made = set(), set()
    for i in g.order:
        t, c = g.t[i], g.cards[i]
        names.update(c["reads"])
        names.update(w[0] for w in c["writes"])
        ir = t.ir
        for f in ir.get("fields", []) if t.kind in ("Select", "Join", "AppendFields") else []:
            names.add(re.sub(r"^(Right_|Source_)", "", f[0]))
        for key in ("keys", "data", "left", "right"):
            names.update(ir.get(key, []) if isinstance(ir.get(key), list) else [])
        if t.kind == "RecordID":
            made.add(ir["name"])
        if t.kind in ("Select", "Join", "AppendFields"):
            made.update(r for f, sel, r, *_ in ir["fields"] if sel and r and r != f)
        if t.kind == "Summarize":
            names.update(f for f, *_ in ir["fields"])
            made.update(r for f, a, r, _ in ir["fields"] if a != "GroupBy" and r and r != f)
        if t.kind == "FindReplace" and ir["replace_mode"] == "Append":
            made.update(ir["append"])
    return sorted(n for n in names - made - {"Name", "Value", "*Unknown", ""} if not n.startswith("Right_"))


class Large(unittest.TestCase):
    def test_large_synthetic_filled_and_run(self):
        d = tempfile.mkdtemp(prefix="steps_big_")
        wb = synth_recon.big(938)
        nb, md, path = generate(d, wb, "big")
        g = guide.Guide(workflow.Workflow(path))
        cols = used_columns(g)
        # headers only: the synthetic stacks each chain onto the next, so rows would double 33 times
        for i in g.order:
            t = g.t[i]
            if t.kind in ("Input", "DynamicInput"):
                p, sheet, _, _ = guide.place(t.ir["file"])
                full = os.path.join(d, *p.split("\\"))
                if p.lower().endswith(".csv"):
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "w", encoding="latin-1") as fh:
                        fh.write(",".join('"%s"' % c for c in cols) + "\n")
                else:
                    xlsx(full, sheet or "Sheet1", cols, [])
        for u in range(1, 10):                     # what the Directory steps look for
            xlsx(os.path.join(d, "Output", "Consolidated %d.xlsx" % u), "Sheet%d" % u, cols, [])
        pasted = fill(nb, md)
        self.assertGreater(pasted, 800)
        rc, log = run(nb)
        self.assertEqual(rc, 0, log[-3000:])
        self.assertTrue("=== done" in log)


if __name__ == "__main__":
    unittest.main()
