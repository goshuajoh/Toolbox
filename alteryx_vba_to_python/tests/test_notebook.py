"""The test notebook and the step-by-step run: real files on disk, every code cell run in order the way
Jupyter runs them (one namespace, the notebook's folder as the working directory)."""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import openpyxl
import pandas as pd

from helpers import ROOT, convert, rt
import synth_recon
from ayx2py import __main__ as cli

sys.path.insert(0, os.path.join(ROOT, "examples"))
import make_examples  # noqa: E402

ROWS = [  # Name, Owner, CreationMode, Scenario, Tags, DQScore, Virtualizable, SizeGB, Access
    (" sales_v1 ", "ana", "Manual", "", "draft", "0.5", "N", "250", "RW"),
    ("risk_v2", "bo", "Auto", "Stress", "certified", "0.95", "Y", "20", "R"),
    ("ops_v3", "ana ", "Manual", "Base", "", "0.7", "N", "120", "W"),
]
COLS = ["Name", "Owner", "CreationMode", "Scenario", "Tags", "DQScore", "Virtualizable", "SizeGB", "Access"]

RUNNER = r"""
import json, sys, traceback
nb = json.load(open(sys.argv[1], encoding="utf-8"))
edits = json.loads(sys.argv[2])
ns = {"__name__": "__main__"}
for n, cell in enumerate(nb["cells"]):
    if cell["cell_type"] != "code":
        continue
    src = "".join(cell["source"])
    for old, new in edits:
        src = src.replace(old, new)
    print("=== cell %d" % n)
    exec(compile(src, "cell%d" % n, "exec"), ns)
print("=== done")
"""


def make_files(folder, legacy_audit=None):
    """The workflow's source files, and what the legacy workflow wrote."""
    os.makedirs(os.path.join(folder, "Input"))
    os.makedirs(os.path.join(folder, "Output"))
    os.makedirs(os.path.join(folder, "legacy"))
    with open(os.path.join(folder, "Input", "datasets_export.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(",".join(COLS) + "\n" + "".join(",".join(r) + "\n" for r in ROWS))
    with open(os.path.join(folder, "Input", "old_rules.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write("Rule\nkeep\n")                       # read by the container that is disabled in the saved file
    # what step 1.4 writes in Alteryx: cleansed text, numbers as numbers, today's Load Date
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Datasets"
    ws.append(COLS + ["Load Date"])
    for r in ROWS:
        ws.append([r[0].strip(), r[1].strip()] + list(r[2:5]) + [float(r[5]), r[6], float(r[7]), r[8], dt.date.today()])
    for where in ("Output", "legacy"):
        wb.save(os.path.join(folder, where, "Consolidated Datasets.xlsx"))
    if legacy_audit is not None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Issues"
        ws.append(list(legacy_audit.columns))
        for r in legacy_audit.itertuples(index=False):
            ws.append(list(r))
        wb.save(os.path.join(folder, "legacy", "Dataset audit.xlsx"))


def generate(folder):
    path = os.path.join(folder, "dataset_audit.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(make_examples.dataset_audit().xml())
    out = os.path.join(folder, "out")
    with open(os.devnull, "w") as quiet:
        old, sys.stdout = sys.stdout, quiet
        try:
            cli.main([path, out, "--runner-notebook"])
        finally:
            sys.stdout = old
    return out


def run_notebook(out, edits=()):
    nb = os.path.join(out, "dataset_audit_test.ipynb")
    p = subprocess.run([sys.executable, "-c", RUNNER, nb, json.dumps(list(edits))], cwd=out, capture_output=True,
                       text=True, encoding="utf-8")
    return p.returncode, p.stdout + p.stderr


class Notebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="nb_")
        make_files(cls.dir)
        cls.out = generate(cls.dir)
        with open(os.path.join(cls.out, "dataset_audit_test.ipynb"), encoding="utf-8") as fh:
            cls.nb = json.load(fh)
        cls.code, cls.log = run_notebook(cls.out, [('r"Output\\Consolidated', 'r"legacy\\Consolidated')])

    def cell(self, n):
        return "".join(self.nb["cells"][n]["source"])

    def test_settings_list_every_input(self):
        settings = self.cell(1)
        self.assertTrue(re.search(r"^    '1': r\"Input\\datasets_export\.csv\", +# 1\.1 Input Data · container 1\. "
                                  r"Import and consolidate$", settings, re.M), settings)
        self.assertTrue("    # '36': r\"Output\\template.xlsx|||Datasets\"," in settings)
        self.assertTrue("    # '64': lambda *tables: tables[0]," in settings)
        self.assertTrue("WRITE = False" in settings)
        kinds = [c["cell_type"] for c in self.nb["cells"]]
        self.assertEqual(kinds.count("code"), 2 + 3 + 3)          # settings, setup, 3 containers, problems/look/compare

    def test_runs_top_to_bottom_and_reports_each_step(self):
        log = self.log
        self.assertEqual(self.code, 0, log)
        self.assertTrue("=== done" in log, log)
        self.assertTrue(re.search(r"^  1\.1 +Input Data +#1 +3 rows × 9 columns$", log, re.M), log)
        self.assertTrue(re.search(r"^  1\.4 +Output Data +#4 +3 rows × 10 columns \(kept in memory, no file written\)$",
                                  log, re.M), log)
        self.assertTrue(re.search(r"^  2\.6 +Dynamic Input +#36 +3 rows × 10 columns$", log, re.M), log)
        self.assertTrue(re.search(r"^  2\.7 +Filter +#41 +True 2, False 1 rows × 10 columns$", log, re.M), log)
        # the step that is not converted fails, the steps that need it are skipped, the others still run
        self.assertTrue(re.search(r"^  2\.18 +RegEx +#64 +NOT CONVERTED: #64: RegEx is not converted", log, re.M), log)
        self.assertTrue(re.search(r"^  2\.25 +Union +#90 +skipped: needs step 2\.18 \(RegEx\), which failed$", log, re.M), log)
        self.assertTrue(re.search(r"^  2\.24 +Select +#66 +2 rows × 10 columns$", log, re.M), log)
        self.assertTrue("  -- 24 ran, 1 failed, 3 skipped" in log, log)
        self.assertTrue("step 2.27 Output Data #92: skipped: needs step 2.26 (Formula), which was skipped" in log, log)

    def test_look_and_compare(self):
        log = self.log
        self.assertTrue("step 1.3 Formula #3: 3 rows × 10 columns" in log, log)
        self.assertTrue("columns: Name (V_String), Owner (V_String)" in log, log)
        self.assertTrue("ours: 3 rows × 10 columns;  legacy: 3 rows × 10 columns\nSAME: every row and column matches" in log, log)
        self.assertTrue("step 2.27 Output Data #92 has no output" in log, log)

    def test_with_a_stand_in_for_the_regex_step(self):
        audit = pd.DataFrame({"Name": ["sales_v1", "ops_v3", "sales_v1", "risk_v2"],
                              "Owner": ["ana", "ana", None, None],
                              "Issue": ["Created by hand", "Created by hand", "No scenario", "x"]})
        d = tempfile.mkdtemp(prefix="nb_")
        make_files(d, audit)
        out = generate(d)
        code, log = run_notebook(out, [("    # '64': lambda", "    '64': lambda"),
                                       ('r"Output\\Dataset audit', 'r"legacy\\Dataset audit')])
        self.assertEqual(code, 0, log)
        self.assertTrue("  -- 28 ran, 0 failed, 0 skipped" in log, log)
        self.assertTrue("Every step that ran has an output." in log, log)
        self.assertTrue(re.search(r"^  2\.27 +Output Data +#92 +\d+ rows × \d+ columns \(kept in memory", log, re.M), log)
        self.assertTrue("rows differ:" in log and "differing cells:" in log, log)


class StepRunner(unittest.TestCase):
    def test_only_pulls_what_it_needs_and_run_still_runs_everything(self):
        d = tempfile.mkdtemp(prefix="nb_")
        make_files(d)
        wb = make_examples.dataset_audit()
        mod = convert(wb, d)[1]
        ctx = rt.Context(workflow_dir=d, stubs={"64": lambda df: df})
        whole = mod.run(ctx)
        self.assertEqual(len(ctx.outputs["92"]), len(whole["92"]))
        ctx = rt.Context(workflow_dir=d)
        s = mod.run(ctx, only="Dataset audit", keep_going=True)
        self.assertTrue("36" in s and "1" not in s)              # container 2 reads files, not container 1's rows
        self.assertTrue(ctx.errors["64"].startswith("NOT CONVERTED"))
        with self.assertRaises(rt.NotConverted):
            mod.run(rt.Context(workflow_dir=d), only=2)
        with self.assertRaises(ValueError):
            mod.run(ctx, only=9)

    def test_compare_rules(self):
        ours = rt.frame(["ID", "Name", "Amount", "Day"], [[1, "Abc", 10.0, "2026-11-02"], [2, None, 3.3333333, "2026-11-03"]])
        legacy = pd.DataFrame({"id": [2, 1], "NAME": ["", "ABC"], "Amount": [3.3333334, 10.0],
                               "Day": [dt.datetime(2026, 11, 3), dt.datetime(2026, 11, 2)]})
        with open(os.devnull, "w") as quiet:
            old, sys.stdout = sys.stdout, quiet
            try:
                by_key = rt.compare(ours, legacy, key="ID")
                by_pos = rt.compare(ours, legacy)
            finally:
                sys.stdout = old
        self.assertEqual(len(by_key), 0)
        self.assertEqual(set(by_pos["column"]), {"ID", "Name", "Amount", "Day"})

    def test_excel_cell_range(self):
        d = tempfile.mkdtemp(prefix="rng_")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S"
        ws.append(["title line"])
        ws.append([None, "A", "B"])
        ws.append([None, 1, "x"])
        ws.append([None, 2, "y"])
        ws.append([None, 3, "z"])
        wb.save(os.path.join(d, "book.xlsx"))
        rt.use(rt.Context(workflow_dir=d))
        df = rt.read_file("book.xlsx|||`S$B2:C4`")
        self.assertEqual(list(df.columns), ["A", "B"])
        self.assertEqual(df["B"].tolist(), ["x", "y"])


class Size(unittest.TestCase):
    def test_large_module_and_notebook(self):
        d = tempfile.mkdtemp(prefix="big_")
        wb = synth_recon.big(938)
        mod = convert(wb, d)[1]
        self.assertEqual(len(mod.STEPS), len({st[0] for st in mod.STEPS}))
        self.assertTrue(len(mod.STEPS) > 850)                      # every tool that runs
        self.assertEqual(sorted({st[1] for st in mod.STEPS}), sorted(mod.CONTAINERS))
        path = os.path.join(d, "wf.yxmd")
        from ayx2py import notebook, workflow
        nb = json.loads(notebook.build_runner(workflow.Workflow(path), "big_generated", d))
        runs = [c for c in nb["cells"] if "wf.run(ctx, only=" in "".join(c["source"])]
        self.assertEqual(len(runs), len(mod.CONTAINERS))


if __name__ == "__main__":
    unittest.main()
