"""A reconciliation-shaped workflow end to end, the factoring of repeated sub-chains, and output size."""
import os
import re
import subprocess
import sys
import tempfile
import unittest

import synth_recon
from helpers import ROOT, convert, rt, table


class ReconShaped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf, cls.mod, cls.lines, cls.info = convert(synth_recon.small())
        cls.ctx = rt.Context()
        cls.s = cls.mod.run(cls.ctx)

    def test_result_matches_hand_computed(self):
        self.assertEqual(table(self.ctx.outputs["303"]), [
            (None, "SYSA000004", "1", "Discrepancy in Side;Discrepancy in Currency", "N", "Discrepancy in Side;Discrepancy in Currency"),
            ("D1", "SYSA000001", "1", "", "Y", "All details matched"),
            ("D2", "SYSA000002", "2", "Discrepancy in Side", "N", "Discrepancy in Side"),
            ("D3", "SYSA000003", "4", "Discrepancy in Contract Amount", "N", "Discrepancy in Contract Amount"),
        ])

    def test_every_tool_converted(self):
        self.assertEqual([t.id for t in self.wf.tools.values() if t.status == "stub"], [])

    def test_tree_is_factored_once(self):
        blocks = self.info["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(blocks[0]), 2)                       # two copies
        self.assertEqual(len(blocks[0][0]), 26)                   # the whole tree: 2 filters, 3 chains, union, result
        text = "\n".join(self.lines)
        self.assertTrue("[B1 copy 2 of 2] 26 tools" in text)
        self.assertTrue("each tool ID = B1's -100" in text)
        self.assertTrue("fed by #10→#101" in text)
        # each rule is written once: the second copy's tools are not listed
        self.assertEqual(text.count("'Discrepancy in Contract Amount' ENDIF"), 1)
        self.assertFalse("#111 " in text)
        self.assertTrue("#211 " in text)

    def test_repeated_rules_are_named(self):
        names = [n for n, _, _ in self.info["rules"]]
        self.assertTrue(names)
        text = "\n".join(self.lines)
        for n in names:
            self.assertTrue("  %s = " % n in text, n)

    def test_generated_code_shares_functions_and_keeps_ids(self):
        with open(self.mod.__file__, encoding="utf-8") as fh:
            code = fh.read()
        self.assertTrue("def t203(df):" in code)
        self.assertFalse("def t103(" in code)                     # same rule as #203: one function
        self.assertTrue("#203 Formula   [Recon T+1]   also: #210 #218 #103 #110 #118" in code)
        self.assertTrue("# If [SIDE]=[SYSB_SIDE] then '' ELSE 'Discrepancy in Side' ENDIF" in code)
        self.assertTrue("s['103'] = t203(" in code)

    def test_output_is_deterministic(self):
        again = convert(synth_recon.small())
        self.assertEqual(self.lines[2:], again[2][2:])            # all but the temp path and hash lines


class Size(unittest.TestCase):
    def test_cli_on_a_production_sized_workflow(self):
        tmp = tempfile.mkdtemp(prefix="ayx_big_")
        wb = synth_recon.big(938)
        src = os.path.join(tmp, "big.txt")                         # .yxmd saved as .txt, with a BOM
        with open(src, "w", encoding="utf-8-sig") as fh:
            fh.write(wb.xml())
        with open(src, "rb") as fh:
            before = fh.read()
        r = subprocess.run([sys.executable, "-m", "ayx2py", src, os.path.join(tmp, "out"), "--tool-listing"], cwd=ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(src, "rb") as fh:
            self.assertEqual(before, fh.read())                     # the workflow is only read
        with open(os.path.join(tmp, "out", "big_logic.md"), encoding="utf-8") as fh:
            spec = fh.read().splitlines()
        with open(os.path.join(tmp, "out", "big_tools.txt"), encoding="utf-8-sig") as fh:
            listing = fh.read().splitlines()
        with open(os.path.join(tmp, "out", "big_generated.py"), encoding="utf-8") as fh:
            code = fh.read()
        compile(code, "big_generated.py", "exec")
        tools = len(wb.items)
        self.assertGreater(tools, 900)
        self.assertLess(len(spec), 5 * tools)
        self.assertEqual(sum(1 for x in spec if re.match(r"### Stages .* — repeat of stages", x)), 3)
        self.assertTrue("copy 4 of 4" in "\n".join(listing))
        print("\n[size] %d tools -> logic %d lines / %d chars; tool listing %d lines; code %d lines / %d chars" % (
            tools, len(spec), sum(len(x) + 1 for x in spec), len(listing), code.count("\n"), len(code)))


if __name__ == "__main__":
    unittest.main()
