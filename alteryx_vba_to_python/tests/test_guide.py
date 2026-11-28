"""The migration guide: containers, one story per output in run order, every step once with its rule and pandas,
columns tables, links, and pandas one-liners that compute what the converter's runtime computes."""
import os
import re
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from helpers import ROOT, evaluate, rt
import logicdoc
import synth_recon
from ayx2py import expr, guide, sketch, workflow

sys.path.insert(0, os.path.join(ROOT, "examples"))
import make_examples  # noqa: E402


def build(wb):
    path = os.path.join(tempfile.mkdtemp(prefix="guide_"), "wf.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    g = guide.Guide(workflow.Workflow(path))
    doc = g.build()
    return g, logicdoc.to_markdown(doc), logicdoc.to_html(doc, "t")


class DatasetAudit(unittest.TestCase):
    """The example laid out like a typical Designer canvas: one input, six annotated filter branches."""

    @classmethod
    def setUpClass(cls):
        cls.g, cls.md, cls.html = build(make_examples.dataset_audit())

    def test_containers_overview(self):
        md = self.md
        self.assertTrue("3 containers, 1 disabled in the saved file (documented anyway), 34 tools, 2 outputs." in md)
        self.assertTrue("### 1. Import and consolidate\n\nIt opens 1 source, calculates columns and saves 1 table, in 4 "
                        "steps.\n\n- **Opens:** `datasets_export.csv`\n- **Saves:** `Consolidated Datasets.xlsx` › "
                        "Datasets\n- **Used by:** container 2" in md, md[:3000])
        self.assertTrue("It opens `Consolidated Datasets.xlsx`, which container 1 saves." in md)
        self.assertTrue("- **Opens:** the list of files in folder `Output` (files named \\*.xlsx); the files listed in "
                        "column [Source] (like `template.xlsx`)" in md)
        self.assertTrue("### 3. Old checks _(disabled in the saved file)_" in md)
        self.assertTrue("Disabled in the saved file: Alteryx skips it until someone enables it" in md)
        self.assertTrue("1. Container 2 runs after container 1: it opens `Consolidated Datasets.xlsx`, which container 1 "
                        "saves." in md)

    def test_one_story_per_output_in_run_order(self):
        md = self.md
        heads = re.findall(r"^#### (.+)$", md, re.M)
        self.assertEqual(heads, ["Consolidated Datasets.xlsx › Datasets", "Dataset audit.xlsx › Issues"])
        self.assertFalse(re.search(r"Branch \d", md))
        story = md[md.index("#### Dataset audit.xlsx › Issues"):md.index("### Container 3.")]
        nums = [int(x) for x in re.findall(r"^\*\*(\d+)\. ", story, re.M)]
        self.assertEqual(nums, list(range(1, 28)))                     # 27 steps, numbered in the order they run
        self.assertTrue("**15. Filter** “Data Quality” · step 2.16 · tool #44  \nKeeps only the rows where [DQScore] < "
                        "0.8.  \nTakes: step 6  \nLooks at: [DQScore]" in story, story[:2000])
        self.assertTrue("**24. Join** · step 2.15 · tool #63  \nMatches its rows with a typed table ([Owner], [Team]; 2 "
                        "rows) on [Owner].  \nTakes: step 14" in story)                # the typed table is read inside it
        self.assertTrue("Stacks the tables step 9, step 12, step 24 (matched rows), step 17, step 20, step 23" in story)
        self.assertTrue('Sets: **Issue** = "Write only" when [Access] is "W", otherwise "Read/write"' in story)
        self.assertTrue("**Not used by any output:** 2 steps (Input Data 1, Formula 1)." in md)   # container 3

    def test_every_step_once_with_its_rule_and_pandas(self):
        md = self.md
        folds = re.findall(r"^- \*\*(\d+\.\d+)\*\* ", md, re.M)
        self.assertEqual(sorted(folds), sorted(self.g.sid[i] for i in self.g.order if i not in self.g.fold_of))
        self.assertTrue('- **2.16** Filter #44 · exact · `t44` · `df = df[(df["DQScore"] < 0.8)]`' in md)
        fold = md[md.index("- **2.17** Formula #54"):md.index("- **2.18** ")]
        self.assertTrue('Issue = "Low data quality: " + ToString([DQScore], 2)\n      # pandas:\n      df["Issue"] = ' in fold, fold)
        self.assertTrue("- **2.18** RegEx #64 · **not converted**: tool kind RegEx has no converter" in md)
        self.assertTrue("**17. RegEx** · step 2.18 · tool #64 · ⚠ not converted: write by hand" in md)
        self.assertTrue("pd.concat([s2_9, s2_12, s2_15_join, s2_18, s2_21, s2_24], ignore_index=True)" in md)
        self.assertTrue('s2_15_join = m[m["_merge"] == "both"]' in md)

    def test_files_show_sheet_cells_and_first_line(self):
        self.assertEqual(guide.place("Book.xlsx|||`Sheet 1$B3:F200`"), ("Book.xlsx", "Sheet 1", "B3:F200", None))
        self.assertEqual(guide.place_text("In\\a.csv", {"ImportLine": "4", "HeaderRow": "False"}),
                         "**a.csv** · from line 4 · no header row")
        self.assertTrue("| `Consolidated Datasets.xlsx` | Datasets | all | saved | — | Consolidated Datasets.xlsx › Datasets "
                        "(1.4) |" in self.md)

    def test_columns_table(self):
        self.assertTrue("| **Issue** | V_WString | 2.8, 2.11, 2.14, 2.17, 2.20, 2.23 |" in self.md)
        row = next(line for line in self.md.splitlines() if line.startswith("| **DQScore** |"))
        self.assertTrue(row.endswith("| 2.16, 2.17 |"), row)

    def test_html(self):
        h = self.html
        self.assertFalse(re.search(r"<script|<link|src=|href=\"http", h))
        ids = set(re.findall(r'id="([^"]+)"', h))
        for target in set(re.findall(r'href="#([^"]+)"', h)):
            self.assertTrue(target in ids, target)
        self.assertTrue('<div class="box story" id="o92"><h3><span class="chip o">Dataset audit.xlsx › Issues</span>' in h)
        self.assertTrue('<li id="s2-16"><div><div class="tool">Filter <i>“Data Quality”</i> <small>· step 2.16 · tool #44'
                        '</small></div>' in h)
        self.assertTrue('<details class="rule"><summary>Rule and pandas</summary><div class="fs"><b>2.17</b> Formula #54' in h)
        self.assertTrue('<pre id="r2-17">' in h)
        self.assertTrue('Keeps only the rows where <span class="col">DQScore</span> &lt; 0.8.' in h)


class Large(unittest.TestCase):
    def test_every_step_once_and_deterministic(self):
        g, md, _ = build(synth_recon.big(938))
        folds = re.findall(r"^- \*\*(\d+\.\d+)\*\* ", md, re.M)
        self.assertEqual(sorted(folds), sorted(g.sid[i] for i in g.order if i not in g.fold_of))
        outputs = [i for i in g.order if g.t[i].kind == "Output"]
        heads = re.findall(r"^#### (.+)$", md, re.M)
        self.assertEqual(len(heads), len(set(heads)))                   # every story and working table has its own name
        self.assertEqual(sorted(set(re.findall(r'<div class="box story" id="(o\d+)"', build(synth_recon.big(938))[2]))),
                         sorted("o" + i for i in outputs))
        self.assertEqual(md, build(synth_recon.big(938))[1])            # deterministic

    def test_small_recon(self):
        g, md, _ = build(synth_recon.small())
        self.assertTrue("Matches its rows with" in md and " on [DEAL ID]." in md)
        self.assertFalse(re.search(r"Branch \d", md))


class Sketches(unittest.TestCase):
    """The pandas one-liners give what the converter's runtime gives, row by row."""

    def check(self, text, rows):
        ns = {"pd": pd, "np": np}
        exec("\n".join(guide.HELPERS), ns)
        df = pd.DataFrame(rows)
        ns["df"] = df
        got = eval(sketch.vec(expr.parse(text)), ns)
        got = list(got) if hasattr(got, "__len__") else [got] * len(df)
        want = [evaluate(text, {k: (None if v is None or v != v else v) for k, v in r.items()}) for r in rows]
        norm = lambda v: None if v is None or (isinstance(v, float) and v != v) else bool(v) if isinstance(v, np.bool_) else v
        self.assertEqual([norm(v) for v in got], want, text)

    def test_against_runtime(self):
        rows = [{"S": "Long", "A": 10.0, "B": 9.5, "T": "a draft", "N": None, "C": "x"},
                {"S": "short", "A": -3.0, "B": 4.0, "T": "CERTIFIED", "N": "", "C": "y"},
                {"S": "other", "A": 0.0, "B": 0.0, "T": "", "N": "v", "C": "z"}]
        for text in ["IF [S]='Long' THEN 'BUY' ELSEIF [S]='Short' THEN 'SELL' ELSE '' ENDIF",
                     "IF ABS([B])>=(ABS([A])*0.9) AND ABS([B])<=(ABS([A])*1.1) THEN '' ELSE 'diff' ENDIF",
                     "IsEmpty([N])", "!IsEmpty([N]) OR [C] = 'Z'", "[C] IN ('x','z')", "Contains([T],'draft')",
                     "Uppercase(Trim([T]))", "Left([S], 2) + '-' + [C]", "[A] * 2 + 1", "REGEX_Replace([T],'^a ','')"]:
            self.check(text, rows)

    def test_refuses_what_has_no_one_liner(self):
        for text in ["FindString([A],'x')", r"REGEX_Replace([A],'\G;','')", "[Row-1:A]"]:
            with self.assertRaises((sketch.NoSketch, expr.ExprError)):
                sketch.vec(expr.parse(text))


if __name__ == "__main__":
    unittest.main()
