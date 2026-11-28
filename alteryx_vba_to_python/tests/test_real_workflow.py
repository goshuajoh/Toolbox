"""What a first run on a real workflow showed: containers saved disabled (run one at a time), many chained Block Until
Done tools, Dynamic Input read-list modes, Run Command steps, a converter failure, and a file read with a Select on
it. Each is documented, ordered without false loops, converted where possible, and reported without stopping."""
import contextlib
import io
import os
import re
import tempfile
import unittest

from helpers import convert, rt, table
import logicdoc
from yx import WB, escape
from ayx2py import __main__ as cli
from ayx2py import codegen, diagnose, guide, workflow

S = "V_String"


def save(wb):
    d = tempfile.mkdtemp(prefix="real_")
    path = os.path.join(d, "wf.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(wb.xml())
    return d, path


def guide_md(path, **kw):
    wf = workflow.Workflow(path, **kw)
    g = guide.Guide(wf)
    return wf, g, logicdoc.to_markdown(g.build())


def last_section(md):
    return md[md.index("## Before relying on this"):]


def cli_run(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(args))
    return rc, buf.getvalue()


def four_containers():
    """(i) Four top-level containers; the team enables one at a time, so three are saved disabled. They share files."""
    wb = WB()
    for n, (name, off) in enumerate((("Import", False), ("Keys", True), ("Recon T+1", True), ("Recon T+2", True))):
        wb.container([name], 900 + n, disabled=off)
    wb.text_input(1, ["Deal", "Amount"], [["D1", "10"], ["D2", "20"]], box=("Import",))
    wb.formula(2, [("Checked", "'yes'")], box=("Import",))
    wb.output(3, "Output\\import.csv", box=("Import",))
    wb.conn(1, 2); wb.conn(2, 3)
    wb.input(10, "Output\\import.csv", box=("Keys",), CodePage=65001, Delimeter=",", HeaderRow="True")
    wb.filter(11, "[Deal] = 'D1'", box=("Keys",))
    wb.output(12, "Output\\keys.csv", box=("Keys",))
    wb.conn(10, 11); wb.conn(11, 12, sa="True")
    wb.input(20, "Output\\keys.csv", box=("Recon T+1",), CodePage=65001, Delimeter=",", HeaderRow="True")
    wb.union(23, box=("Recon T+1",))
    wb.formula(21, [("Reconciled", "'Y'")], box=("Recon T+1",))
    wb.output(22, "Output\\recon.csv", box=("Recon T+1",))
    wb.conn(20, 23, name="#1"); wb.conn(23, 21); wb.conn(21, 22)
    wb.input(30, "Output\\recon.csv", box=("Recon T+2",), CodePage=65001, Delimeter=",", HeaderRow="True")
    wb.formula(31, [("Second", "'T+2'")], box=("Recon T+2",))
    wb.output(32, "Output\\history.csv", box=("Recon T+2",))
    wb.conn(30, 31); wb.conn(31, 32)
    wb.conn(2, 23, name="#2")                        # a direct connection from the enabled into a disabled container
    return wb


class DisabledContainers(unittest.TestCase):
    def test_documented_converted_and_runnable_one_at_a_time(self):
        d, path = save(four_containers())
        wf, g, md = guide_md(path)
        self.assertTrue("4 containers, 3 disabled in the saved file (documented anyway), 13 tools" in md)
        for n, name in ((2, "Keys"), (3, "Recon T+1"), (4, "Recon T+2")):
            self.assertTrue("### %d. %s _(disabled in the saved file)_" % (n, name) in md, name)
            self.assertTrue("in the generated code run it with `only=%d`" % n in md, name)
        self.assertTrue("#### recon.csv _(container 3 · disabled in the saved file)_" in md)
        self.assertTrue('| 2 | Sets 1 column:<br>**Reconciled** = "Y" |' in md)                                     # explained all the same
        self.assertTrue("It takes rows from **Table A** (container 1); it opens "
                        "`keys.csv`, which container 2 saves." in md)                         # links still shown
        self.assertEqual(g.errors, [])
        # generated code: the saved file's behaviour by default, any container on request
        os.makedirs(os.path.join(d, "Output"))
        with open(os.path.join(d, "Output", "import.csv"), "w") as fh:
            fh.write("Deal,Amount,Checked\nD1,10,yes\nD2,20,yes\n")
        wf2, mod = convert(four_containers(), d)[:2]
        self.assertEqual(mod.DISABLED, {2, 3, 4})
        s = mod.run(rt.Context(workflow_dir=d))
        self.assertEqual(sorted(s), ["1", "2", "3"])
        s = mod.run(rt.Context(workflow_dir=d), only=2)
        self.assertEqual(len(s["11"]["True"]), 1)
        # --enabled-only: the old behaviour
        _, _, md_old = guide_md(path, enabled_only=True)
        self.assertTrue("### 2. Keys _(disabled in the saved file)_\n\nLeft out: the guide was made with --enabled-only."
                        in md_old)

    def test_notebooks_put_disabled_containers_last(self):
        d, path = save(four_containers())
        rc, out = cli_run(path, os.path.join(d, "out"), "--runner-notebook")
        self.assertEqual(rc, 0, out)
        import json
        for name in ("wf_steps.ipynb", "wf_test.ipynb"):
            with open(os.path.join(d, "out", name), encoding="utf-8") as fh:
                cells = ["".join(c["source"]) for c in json.load(fh)["cells"]]
            heads = [c for c in cells if c.startswith("## ")]
            at = heads.index("## Containers disabled in the saved file\n\n" + next(
                h for h in heads if h.startswith("## Containers disabled")).split("\n\n", 1)[1])
            self.assertTrue(any(h.startswith("## 2. Keys (disabled in the saved file)") for h in heads[at:]), name)
            self.assertTrue(all("disabled" not in h for h in heads[:at] if h.startswith("## 1.")), name)


def bud_web():
    """(ii) 40 Block Until Done / Parallel Block Until Done tools, chained and nested, fanning out to several outputs,
    plus one pair whose preferences the connections contradict."""
    wb = WB()
    wb.text_input(1, ["A"], [["1"]])
    prev, tid = (1, "Output"), 100
    for n in range(19):
        b = tid
        if n % 2:
            wb.pbud(b)
            wb.conn(prev[0], b, sa=prev[1], da="Input1")
            wb.conn(prev[0], b, sa=prev[1], da="Input2")
            wb.conn(prev[0], b, sa=prev[1], da="Input3")
        else:
            wb.node(b, "BlockUntilDone", "")
            wb.conn(prev[0], b, sa=prev[1])
        outs = ["Output1", "Output2", "Output3"]
        for k, a in enumerate(outs):
            f = tid + 10 + k
            wb.formula(f, [("S%d_%d" % (n, k), "'x'")])
            wb.conn(b, f, sa=a)
            if k < 2:
                wb.output(f + 50, "Output\\o_%d_%d.csv" % (n, k))
                wb.conn(f, f + 50)
        prev, tid = (tid + 12, "Output"), tid + 100
    # two that contradict each other through the connections: X wants s1 before f2, Y wants s2 before f1, while
    # f2 feeds s2 and f1 feeds s1. One preference has to go; the old code made a "loop" of it.
    wb.node(5000, "BlockUntilDone", ""); wb.conn(1, 5000)                              # X
    wb.node(5100, "BlockUntilDone", ""); wb.conn(1, 5100)                              # Y
    wb.formula(5001, [("a", "'a'")]); wb.conn(5000, 5001, sa="Output1")
    wb.formula(5002, [("f2", "'f2'")]); wb.conn(5000, 5002, sa="Output2")
    wb.formula(5101, [("b", "'b'")]); wb.conn(5100, 5101, sa="Output1")
    wb.formula(5102, [("f1", "'f1'")]); wb.conn(5100, 5102, sa="Output2")
    wb.union(5003); wb.conn(5001, 5003, name="#1"); wb.conn(5102, 5003, name="#2")      # s1
    wb.union(5103); wb.conn(5101, 5103, name="#1"); wb.conn(5002, 5103, name="#2")      # s2
    return wb


class BlockUntilDone(unittest.TestCase):
    def test_no_false_loop_and_a_sensible_order(self):
        d, path = save(bud_web())
        wf, g, md = guide_md(path)
        buds = [i for i in wf.ordered() if wf.tools[i].kind in ("BlockUntilDone", "ParallelBlockUntilDone")]
        self.assertEqual(len(buds), 21)                             # 19 in the chain, X and Y
        self.assertEqual(wf.cycle_edges, [])
        self.assertFalse("loop" in md.lower())
        self.assertEqual(g.errors, [])
        rows = re.findall(r"^\| (\d+\.\d+) \| ", md, re.M)
        self.assertEqual(len(set(rows)), len(wf.live))                                 # every tool has its row
        for b in buds:                                              # output 1's work comes before output 2's
            o1 = [e[2] for e in wf.outs[b] if e[1] == "Output1"]
            o2 = [e[2] for e in wf.outs[b] if e[1] == "Output2"]
            if b not in ("5000", "5100"):
                self.assertLess(max(wf.rank[x] for x in wf.reach([(0, 0, i) for i in o1]) | set(o1)),
                                min(wf.rank[x] for x in o2), b)
        self.assertEqual(len(wf.dropped), 1)
        self.assertTrue(wf.dropped[0] in (("5003", "5002"), ("5103", "5102")), wf.dropped)
        text = last_section(md)
        self.assertTrue("**Run order: 1 Block Until Done preference could not be honoured**" in text, text)
        self.assertTrue(re.search(r"could not honour: step [\d.]+ before step [\d.]+", text), text)
        mod = convert(bud_web())[1]                                     # and the generated code runs
        s = mod.run(rt.Context())
        self.assertEqual(len(s["5003"]), 2)


def dyn(wb, tid, template, field, how):
    return wb.node(tid, "DynamicInput", '<InputConfiguration><Configuration><Passwords /><File FileFormat="0">%s</File>'
                   '<FormatSpecificOptions><HeaderRow>True</HeaderRow><Delimeter>,</Delimeter></FormatSpecificOptions>'
                   '</Configuration></InputConfiguration><Mode>ReadList</Mode><ReadList_Field>%s</ReadList_Field>'
                   '<ReadList_Type>%s</ReadList_Type>' % (escape(template), escape(field), escape(how)))


class DynamicInput(unittest.TestCase):
    MODES = [("Path", "data\\a.csv", "whole path"), ("FileName", "b.csv", "file name"),
             ("Prepend", "2026_", "before its file name"), ("Append", "_2026", "added to its file name")]

    def test_each_read_list_mode_described_and_run_on_files(self):
        wb = WB()
        for n, (how, value, _) in enumerate(self.MODES):
            wb.text_input(10 + n, ["F"], [[value]])
            dyn(wb, 20 + n, "data\\sales.csv", "F", how)
            wb.conn(10 + n, 20 + n)
        wb.text_input(14, ["F"], [["x"]])
        dyn(wb, 24, "data\\sales.csv", "F", "SomethingNew")
        wb.conn(14, 24)
        d, path = save(wb)
        os.makedirs(os.path.join(d, "data"))
        for name, v in (("a.csv", "a"), ("b.csv", "b"), ("2026_sales.csv", "pre"), ("sales_2026.csv", "suf")):
            with open(os.path.join(d, "data", name), "w") as fh:
                fh.write("V\n%s\n" % v)
        wf, g, md = guide_md(path)
        self.assertEqual([wf.tools[str(20 + n)].status for n in range(5)], ["caveat"] * 4 + ["stub"])
        self.assertTrue("Opens every file listed in column [F] (the column holds the whole path" in md)
        self.assertTrue("Opens the file named in column [F], in folder `data`" in md)
        self.assertTrue("with column [F] put before its file name" in md)
        self.assertTrue("with column [F] added to its file name" in md)
        self.assertTrue("Opens the files given by column [F] (mode ReadList/SomethingNew, not known to the converter" in md)
        mod = convert(wb, d)[1]
        ctx = rt.Context(workflow_dir=d)
        s = mod.run(ctx, keep_going=True)
        self.assertEqual([table(s[str(20 + n)]) for n in range(4)], [[("a",)], [("b",)], [("pre",)], [("suf",)]])
        self.assertTrue(ctx.errors["24"].startswith("NOT CONVERTED"))
        text = last_section(md)
        self.assertTrue("DynamicInput, 1 step" in text and "SomethingNew is not known to the converter" in text, text)
        # the guide's pandas lines run too
        from test_steps_notebook import fill, generate, run
        nb, md2, _ = generate(d, wb, "dyn")
        fill(nb, md2)
        rc, log = run(nb)
        self.assertEqual(rc, 0, log[-1500:])


class RunCommand(unittest.TestCase):
    def test_described_and_a_logging_placeholder(self):
        wb = WB()
        wb.text_input(1, ["A"], [["1"]])
        wb.pbud(2); wb.conn(1, 2, da="Input1")
        wb.output(3, "Output\\for_macro.csv"); wb.conn(2, 3, sa="Output1")
        wb.run_command(4, "cscript.exe", "ImportAll.vbs Configuration.xlsm"); wb.conn(2, 4, sa="Output2")
        wb.formula(5, [("After", "'done'")]); wb.conn(4, 5)
        d, path = save(wb)
        wf, g, md = guide_md(path)
        self.assertTrue("| Runs `cscript.exe ImportAll.vbs Configuration.xlsm` outside Alteryx. |  | Run Command · step 1.4 · tool #4 ⚠ "
                        "outside Alteryx: write by hand |" in md)
        self.assertTrue("**1 external program to replace** (Run Command)" in last_section(md))
        self.assertFalse("Not converted" in last_section(md))
        mod = convert(wb, d)[1]
        ctx = rt.Context(workflow_dir=d)
        s = mod.run(ctx)                                                # does not stop the container
        self.assertEqual(table(s["5"]), [(1, "done")])
        self.assertTrue(any("external program not run: cscript.exe ImportAll.vbs" in x for x in ctx.log))
        rc, out = cli_run(path, os.path.join(d, "out"))
        self.assertTrue("1 external program to replace (Run Command; the generated code only logs them): #4" in out, out)


class Loops(unittest.TestCase):
    def test_a_real_loop_is_reported_and_the_guide_still_written(self):
        wb = WB()
        wb.text_input(1, ["A"], [["1"]])
        wb.formula(2, [("B", "'b'")]); wb.formula(3, [("C", "'c'")]); wb.formula(4, [("D", "'d'")])
        wb.conn(1, 2); wb.conn(2, 3); wb.conn(3, 4); wb.conn(4, 2)
        wb.conns.append('<Connection><Origin ToolID="4" Connection="Output" /><Destination ToolID="99" Connection="Input" />'
                        '</Connection>')
        d, path = save(wb)
        wf, g, md = guide_md(path)
        self.assertEqual(sorted((e[0], e[2]) for e in wf.cycle_edges), [("2", "3"), ("3", "4"), ("4", "2")])
        text = last_section(md)
        self.assertTrue("**The connections form a loop**" in text, text)
        self.assertTrue("**1 connection to a tool that is not in the file** (ignored): #4 → #99" in text, text)
        rc, out = cli_run(path, os.path.join(d, "out"), "--diagnose", os.path.join(d, "diag.txt"))
        self.assertEqual(rc, 0, out)
        with open(os.path.join(d, "diag.txt"), encoding="utf-8") as fh:
            rep = fh.read()
        self.assertTrue("real loops: 3 connections" in rep and "#4 Formula -> #2 Formula" in rep, rep)


class Failures(unittest.TestCase):
    def test_a_failure_is_named_and_the_step_still_shown(self):
        wb = WB()
        wb.text_input(1, ["A"], [["1"]])
        wb.formula(2, [("B", "[A] + 'x'")])
        wb.conn(1, 2)
        d, path = save(wb)
        real = guide.Guide.card

        def boom(self, i):
            if i == "2":
                raise ValueError("simulated")
            return real(self, i)

        guide.Guide.card = boom
        try:
            wf, g, md = guide_md(path)
        finally:
            guide.Guide.card = real
        self.assertEqual(g.errors, [("ValueError", "guide.safe_card", "2", "Formula")])
        self.assertTrue('- (1.2) Formula #2: Sets 1 column: **B** = [A] + "x" · ⚠ not converted: write by hand' in md, md)
        self.assertTrue("- **1.2** Formula #2 · **not converted**: the converter failed on this step (ValueError in "
                        "guide.safe_card)" in md)
        self.assertTrue("      setting: B [V_WString] = [A] + 'x'" in md)                       # its settings, from the XML
        self.assertTrue("**The converter failed on 1 step** (please report" in md and
                        "ValueError in guide.safe_card: steps 1.2" in md)

    def test_a_file_read_with_a_select_inside_a_join(self):
        """The run that reported 'expression nested too deeply' for Select and Find Replace steps: a folded read with
        a Select on it made the step that uses it describe the Select, which described itself again."""
        wb = WB()
        wb.text_input(1, ["Key", "A"], [["k", "1"]])
        wb.input(2, "Config\\clients.xlsx|||`DAC Client$`")
        wb.select(3, [("Key", True), ("Name", True, "Client"), ("*Unknown", False)])
        wb.join(4, ["Key"], ["Key"])
        wb.conn(2, 3); wb.conn(1, 4, da="Left"); wb.conn(3, 4, da="Right")
        d, path = save(wb)
        wf, g, md = guide_md(path)
        self.assertEqual(g.errors, [])
        self.assertTrue("Matches the rows with `clients.xlsx › DAC Client` on [Key]." in md)
        self.assertTrue("src_clients_dac_client_1_2b = src_clients_dac_client.copy()" in md)
        self.assertTrue('src_clients_dac_client_1_2b = src_clients_dac_client_1_2b.rename(columns={"Name": "Client"})' in md)


class Diagnose(unittest.TestCase):
    def test_structure_only(self):
        wb = four_containers()
        wb.notes = {}
        wb.container(["SecretCaption"], 950)
        wb.formula(40, [("SecretField", "IF [SecretInput] = 'SecretValue' THEN 1 ELSE 2 ENDIF")], box=("SecretCaption",),
                   note="SecretNote")
        wb.raw(41, "RegEx", "<Field>SecretField</Field>", box=("SecretCaption",))
        wb.input(42, "C:\\Secret\\secret_file.xlsx|||`SecretSheet$`", box=("SecretCaption",))
        wb.conn(42, 40); wb.conn(40, 41)
        d, path = save(wb)
        rc, out = cli_run(path, os.path.join(d, "out"), "--diagnose", os.path.join(d, "diag.txt"))
        self.assertEqual(rc, 0, out)
        with open(os.path.join(d, "diag.txt"), encoding="utf-8") as fh:
            rep = fh.read()
        for secret in ("Secret", "secret", "Import", "Keys", "Recon", "Deal", "import.csv", "D1"):
            self.assertNotIn(secret, rep)
        self.assertTrue(rep.startswith("ayx2py structure report"))
        self.assertTrue("top-level containers 5 (disabled 3)" in rep)
        self.assertTrue("C2 disabled tools 3 depth 1" in rep)
        self.assertTrue("RegEx 1; no converter" in rep)
        self.assertTrue("Formula: Output | Input" in rep)


if __name__ == "__main__":
    unittest.main()
