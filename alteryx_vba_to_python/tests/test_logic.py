"""The plain-language logic documents: stages, wording of rules, determinism, links, and that the
converters handle workflows and macros unlike the reconciliation they were first written for."""
import os
import re
import sys
import tempfile
import unittest

from helpers import ROOT, convert, rt, table
import logicdoc
import synth_recon
from ayx2py import expr, logic, workflow

sys.path.insert(0, os.path.join(ROOT, "examples"))
import make_examples  # noqa: E402


def logic_md(wb):
    tmp = tempfile.mkdtemp(prefix="logic_")
    path = os.path.join(tmp, "wf.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    doc = logic.build(workflow.Workflow(path))
    return doc, logicdoc.to_markdown(doc), logicdoc.to_html(doc, "t")


class Readable(unittest.TestCase):
    def r(self, text, width=100):
        return expr.decision(expr.parse(text), width)

    def test_patterns(self):
        self.assertEqual(self.r("[C] = 'A' OR [C] = 'B' OR [C] = 'C'"), ['[C] is one of "A", "B", "C"'])
        self.assertEqual(self.r('Contains([R],"X") OR Contains([R],"Y")'), ['[R] contains any of "X", "Y"'])
        self.assertEqual(self.r("[Q] != 'a' AND [Q] != 'b'"), ['[Q] is none of "a", "b"'])
        self.assertEqual(self.r("[A] >= 1 AND [A] <= 2"), ["[A] between 1 and 2"])
        self.assertEqual(self.r("!IsEmpty([D]) AND IsNull([E])"), ["[D] is not empty and [E] is null"])
        self.assertEqual(self.r("[A] = 1 OR [B] = 2 AND [C] = 3"), ["([A] = 1 or [B] = 2) and [C] = 3"])   # Alteryx order shown
        self.assertEqual(self.r("[A] - ([B] - [C]) * 2 / 4"), ["[A] - ([B] - [C]) × 2 ÷ 4"])
        self.assertEqual(self.r("IF [A] THEN 'x' ELSE 'y' ENDIF"), ['"x" when [A], otherwise "y"'])

    def test_decision_list(self):
        self.assertEqual(self.r("IF [L]='Long' THEN 'BUY' ELSEIF [L]='Short' THEN 'SELL' ELSE '' ENDIF"),
                         ['when [L] = "Long"  → "BUY"', 'when [L] = "Short" → "SELL"', 'otherwise          → ""'])
        long = self.r("IF [A]=[B] AND [C]=[D] AND [E]>=[F]*0.9 AND [E]<=[F]*1.1 AND [G]=[H] THEN '' ELSE 'bad' ENDIF", 40)
        self.assertEqual(long, ["when [A] = [B]", "     and [C] = [D]", "     and [E] between [F] × 0.9 and [F] × 1.1",
                                "     and [G] = [H]", '     → ""', 'otherwise → "bad"'])


class WorkflowDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.md, cls.html = logic_md(synth_recon.small())

    def test_stages_and_repeat(self):
        md = self.md
        self.assertTrue("## Stage map" in md and "## Before relying on this" in md)
        self.assertTrue("**Size:** 65 tools that run, in 30 stages; stages 18–29 repeat stages 6–17." in md)
        self.assertTrue("### Stages 18–29 — repeat of stages 6–17" in md)
        self.assertTrue("- takes stage 4 (matched rows) where the original takes stage 5 (matched rows)" in md)
        self.assertTrue("2. Set **Discrepancy in Currency** = \"\" when [CCY1] = [SYSB_CCY1], otherwise "
                        "\"Discrepancy in Currency\" (#203)" in md)
        self.assertTrue("1. Split the rows by the test [SYSB_RISKCLASS] contains any of \"TYPEA\", \"TYPEB\" (#201)" in md)
        self.assertTrue("   - rows that pass → stage 7\n   - rows that fail → stage 10" in md)
        self.assertTrue("1. Add **Discrepancies** from the first row of stage 8 whose [Unique ID] equals this row's "
                        "[Unique ID] (matching case; empty when no row matches) (#207)" in md)
        self.assertTrue("**In:** stage 6 (rows that pass).  **Out:** stages 8 and 9." in md)
        self.assertFalse("#111 " in md or "(#111)" in md)                     # the copy's tools are not repeated

    def test_html_is_self_contained_and_links_resolve(self):
        h = self.html
        self.assertFalse(re.search(r"<script|<link|src=|href=\"http", h))
        self.assertTrue("<svg" in h)
        ids = set(re.findall(r'id="([^"]+)"', h))
        for target in set(re.findall(r'href="#([^"]+)"', h)):
            self.assertTrue(target in ids, target)

    def test_deterministic(self):
        self.assertEqual(self.md.split("\n", 3)[3], logic_md(synth_recon.small())[1].split("\n", 3)[3])

    def test_dead_end_and_stub(self):
        from yx import WB
        wb = WB()
        wb.text_input(1, ["A"], [["1"]])
        wb.sort(2, [("A", "Ascending")])
        wb.run_command(3, "cscript.exe", "job.vbs")
        wb.conn(1, 2); wb.conn(1, 3)
        _, md, _ = logic_md(wb)
        self.assertTrue("The rows are not used after this (the stream ends here)" in md)
        self.assertTrue("Run the external command `cscript.exe job.vbs` (#3) — not converted to Python" in md)
        self.assertTrue("**runs** 1 external command" in md)


class Examples(unittest.TestCase):
    """The examples are not reconciliation workflows: the same tools handle them."""

    def test_example_files_are_current(self):
        files = make_examples.main(write=False)
        for name, text in files.items():
            with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text, name + " is stale: run python examples/make_examples.py")

    def test_sales_workflow_runs_in_python(self):
        wb = make_examples.sales_workflow()
        orders = rt.frame(["OrderID", "OrderDate", "Region", "Product", "Qty", "UnitPrice", "Status"], [
            ["1", "03/11/2026", " North ", "Widget", "100", "60", "Open"],      # 6000, 5% off -> 5700
            ["2", "04/11/2026", "South", "Gadget", "2", "10", "Open"],
            ["2", "04/11/2026", "South", "Gadget", "2", "10", "Open"],          # duplicate order
            ["3", "05/11/2026", "North", "Mystery", "1", "5", "Open"],
            ["4", "05/11/2026", "North", "Widget", "1", "5", "Cancelled"]],
            {c: "V_String" for c in ("OrderID", "OrderDate", "Region", "Product", "Qty", "UnitPrice", "Status")})
        wf, mod, _, _ = convert(wb)
        ctx = rt.Context(inputs={"1": orders})
        mod.run(ctx)
        self.assertEqual(table(ctx.outputs["15"]), [("North", "2026-11", "Hardware", 5700.0, 1),
                                                    ("North", "2026-11", "Unknown", 5.0, 1),
                                                    ("South", "2026-11", "Hardware", 20.0, 1)])
        self.assertEqual(rt.rows_of(ctx.outputs["8"])[0]["OrderID"], "2")
        self.assertEqual(table(ctx.outputs["17"]), [("North", 1)])

    def test_month_end_document(self):
        with open(os.path.join(ROOT, "examples", "month_end_logic.md"), encoding="utf-8") as fh:
            md = fh.read()
        for expected in ('- **Reports in:** Control!C3: "Imported " & (lastRow - 1) & " rows", or "Import failed: " & '
                         "the error message",
                         "5. Copy [{srcPath}]Ledger!A2:H{lastRow} to Data!A2 (values only)",
                         '       =IF(F2="DR",G2,-G2)   (the same in A1 notation, in I2)',
                         "2. For **r** = 2 to the last filled row of column A:",
                         "2. Copy the sheet Data into a new workbook, which becomes the active workbook"):
            self.assertTrue(expected in md, expected)


if __name__ == "__main__":
    unittest.main()
