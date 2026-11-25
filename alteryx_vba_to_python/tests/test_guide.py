"""The migration guide: containers, branches in flow order, step cards, columns tables, links, and
pandas one-liners that compute what the converter's runtime computes."""
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
        self.assertTrue("| 1. Import and consolidate | yes | 4 | 1 | datasets_export.csv | Consolidated Datasets.xlsx · sheet "
                        "**Datasets** | — | 2. Dataset audit |" in md)
        self.assertTrue("| 3. Old checks | no (disabled) | 0 | 0 |" in md)
        self.assertTrue("| 1.4 | saves | `Output\\Consolidated Datasets.xlsx` | Datasets | all | replaces the sheet |" in md)
        self.assertTrue("| 2.6 | opens each file listed in [Source], like | `Output\\template.xlsx` | Datasets | all |  |" in md)
        self.assertTrue("- **Fed by:** 1. Import and consolidate (file `Output\\Consolidated Datasets.xlsx`)" in md)
        self.assertTrue("- **Containers inside:** Checks" in md)
        self.assertTrue("Disabled in the workflow: its 2 tools do not run" in md)

    def test_branches_follow_the_flow_and_carry_annotations(self):
        heads = re.findall(r"^#### Branch (\S+) — ([^·]+) ·", self.md, re.M)
        self.assertEqual(heads[1:9], [("2.1", "Newest consolidated file"), ("2.2", "CreationMode"), ("2.3", "Scenario"),
                                      ("2.4", "Tags"), ("2.5", "Data Quality"), ("2.6", "Virtualizable"),
                                      ("2.7", "Read/Write"), ("2.8", "All issues")])
        self.assertFalse("Typed table" in self.md)                 # it is read inside the Join that uses it
        self.assertTrue(self.md.index("### Inside Checks") < self.md.index("#### Branch 2.2 "))

    def test_one_table_per_branch_reads_like_the_canvas(self):
        md = self.md
        tables = re.findall(r"^\| Step \| Tool \| What it does \| New / changed columns \| Next \|\n\|.*\n((?:\|.*\n)+)", md, re.M)
        steps = [re.fullmatch(r"\| (\S+) \| (.*?) \| (.*?) \| (.*?) \| (.*?) \|", line).groups()
                 for t in tables for line in t.splitlines()]
        self.assertEqual(len(steps), 4 + 27)                     # every step once; the typed table is read inside 2.15
        by = {r[0]: r[1:] for r in steps}
        self.assertEqual(by["1.1"], ("Input Data", "opens `datasets_export.csv`", "9 columns", "1.2"))
        self.assertEqual(by["2.16"], ("Filter “Data Quality”", "keeps only the rows where [DQScore] < 0.8", "", "T → 2.17; F dropped"))
        self.assertEqual(by["2.17"][2], "**Issue**")
        self.assertEqual(by["2.15"], ("Join", "matches its rows with a typed table ([Owner], [Team]; 2 rows) on [Owner]", "",
                                      "J → 2.25; L, R dropped"))
        self.assertEqual(by["2.18"][0], "RegEx ⚠")
        self.assertTrue(by["2.27"][1].startswith("saves the table to `Dataset audit.xlsx`, sheet `Issues`"))
        self.assertFalse("**Reads:**" in md or "- **Does:**" in md)
        # rule and pandas sit in one folded line per step, under the table
        self.assertTrue('- **2.16** Filter #44 · exact · `t44` · `df = df[(df["DQScore"] < 0.8)]`' in md)
        fold = md[md.index("- **2.17** Formula #54"):md.index("- **2.18** ")]
        self.assertTrue('Issue = "Low data quality: " + ToString([DQScore], 2)\n      # pandas:\n      df["Issue"] = ' in fold, fold)
        self.assertTrue("- **2.18** RegEx #64 · **not converted**: tool kind RegEx has no converter" in md)
        self.assertTrue("pd.concat([s2_9, s2_12, s2_15_join, s2_18, s2_21, s2_24], ignore_index=True)" in md)
        self.assertTrue('s2_15_join = m[m["_merge"] == "both"]' in md)

    def test_files_show_sheet_cells_and_first_line(self):
        self.assertEqual(guide.place("Book.xlsx|||`Sheet 1$B3:F200`"), ("Book.xlsx", "Sheet 1", "B3:F200", None))
        self.assertEqual(guide.place_text("In\\a.csv", {"ImportLine": "4", "HeaderRow": "False"}),
                         "**a.csv** · from line 4 · no header row")

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
        self.assertTrue('<details class="d2" id="c2">' in h and '<details class="d4" id="b2-5">' in h)
        self.assertTrue('<tr id="s2-16"><td><a href="#r2-16">2.16</a></td>' in h)
        self.assertTrue('<details class="fold"><summary><b>2.17</b> Formula #54' in h and '<pre class="rule" id="r2-17">' in h)


class Large(unittest.TestCase):
    def test_every_tool_once_and_repeats_mapped(self):
        g, md, _ = build(synth_recon.big(938))
        cards = re.findall(r"^- \*\*(\d+\.\d+)\*\* ", md, re.M)
        self.assertEqual(len(cards), len(set(cards)))
        copies = [i for i in g.order if g.L.stage_of[i] in g.L.copy_of]
        self.assertEqual(len(cards) + len(copies), len(g.order) - len(g.fold_of))   # a folded read is not a step
        self.assertEqual(len(re.findall(r"^#### Branches .* — repeat of", md, re.M)), 3)
        self.assertEqual(md, build(synth_recon.big(938))[1])            # deterministic

    def test_small_recon(self):
        g, md, _ = build(synth_recon.small())
        self.assertTrue("#### Branches 2.16–27 — repeat of branches 2.4–15 · steps 2.34–59" in md)
        self.assertTrue("Step for step: 2.34–59 here is 2.8–33 there, in the same order." in md)
        self.assertTrue("| 2.2 | Join on [DEAL ID] | 2.5 |" in md)


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
