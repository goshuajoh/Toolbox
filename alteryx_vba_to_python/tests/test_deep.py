"""Rules with thousands of terms or hundreds of nesting levels: parsed, described in the guide, generated as Python
that compiles and computes the right values, and given pandas lines that run when pasted into the steps notebook."""
import json
import os
import re
import sys
import tempfile
import unittest

import pandas as pd

from helpers import convert, rt
from ayx2py import codegen, expr, guide, workflow
from test_steps_notebook import fill, generate, run
from yx import WB

N = 1500
CASES = {   # name: (tool, rule, the column it makes or None for a filter, expected values on DATA)
    "or2000": ("Filter", " OR ".join('[Cpty] = "C%d"' % i for i in range(2000)), None, ["C5", "c1999"]),
    "or5000": ("Filter", " OR ".join('[Cpty] = "C%d"' % i for i in range(5000)), None, ["C5", "c1999", "C4999"]),
    "plus1500": ("Formula", " + ".join("[F%d]" % i for i in range(N)), "Out", [float(N)] * 4),
    "elseif800": ("Formula", "IF [A]=1 THEN 'x' " + " ".join("ELSEIF [A]=%d THEN 'v%d'" % (i, i) for i in range(2, 800))
                  + " ELSE 'z' ENDIF", "Out", ["x", "v5", "v799", "z"]),
    "nestedif300": ("Formula", "".join("IF [A]=%d THEN 'v%d' ELSE " % (i, i) for i in range(300)) + "'z'" + " ENDIF" * 300,
                    "Out", ["v1", "v5", "v299", "z"]),
    "paren400": ("Formula", "(" * 400 + "[S]" + ")" * 400, "Out", [" ab ", "cd", "", " e"]),
    "paren1000": ("Formula", "(" * 1000 + "[S]" + ")" * 1000, "Out", [" ab ", "cd", "", " e"]),
    "func200": ("Formula", "Trim(" * 200 + "[S]" + ")" * 200, "Out", ["ab", "cd", "", "e"]),
}
DATA = pd.DataFrame({"Cpty": ["C5", "c1999", "X", "C4999"], "A": [1.0, 5.0, 799.0, 900.0], "S": [" ab ", "cd", "", " e"],
                     **{"F%d" % i: [1.0] * 4 for i in range(N)}})
DATA.loc[2, "S"] = ""
ROWS_FOR = {"nestedif300": [1.0, 5.0, 299.0, 400.0]}


def workflow_for(name):
    tool, rule, out, _ = CASES[name]
    wb = WB()
    types = {"Cpty": "V_String", "A": "Double", "S": "V_String", **{"F%d" % i: "Double" for i in range(N)}}
    wb.input(1, "Input\\data.csv", types, CodePage=65001, Delimeter=",", HeaderRow="True")
    if tool == "Filter":
        wb.filter(2, rule)
    else:
        wb.formula(2, [(out, rule, "Double" if name == "plus1500" else "V_WString")])
    wb.output(3, "Output\\out.csv")
    wb.conn(1, 2)
    wb.conn(2, 3, sa="True" if tool == "Filter" else "Output")
    return wb


def data_for(name):
    df = DATA.copy()
    if name in ROWS_FOR:
        df["A"] = ROWS_FOR[name]
    return df


class Deep(unittest.TestCase):
    def check(self, name):
        tool, rule, out, want = CASES[name]
        node = expr.parse(rule)
        self.assertLessEqual(expr.depth(node), 3 if "paren" not in name and "func" not in name else 205, name)
        d = tempfile.mkdtemp(prefix="deep_")
        wb = workflow_for(name)
        # generated module: compiles, imports, and computes the expected values
        wf, mod = convert(wb, d)[:2]
        self.assertEqual(codegen.deep_tools(wf), [], name)
        ctx = rt.Context(workflow_dir=d, inputs={"1": data_for(name)})
        s = mod.run(ctx)
        got = s["2"]["True"]["Cpty"].tolist() if tool == "Filter" else s["2"][out].tolist()
        self.assertEqual(got, want, name)
        # guide and steps notebook: the pasted pandas lines run and give the same values
        os.makedirs(os.path.join(d, "Input"), exist_ok=True)
        data_for(name).to_csv(os.path.join(d, "Input", "data.csv"), index=False, encoding="utf-8-sig")
        nb, md, _ = generate(d, wb, name)
        self.assertEqual(fill(nb, md), 2, name)
        with open(nb, encoding="utf-8") as fh:
            book = json.load(fh)
        col = "Cpty" if tool == "Filter" else out
        book["cells"].append({"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
                              "source": 'import json\nprint("RESULT", json.dumps([None if x != x else x for x in s1_3[%r].tolist()]))'
                                        % col})
        with open(nb, "w", encoding="utf-8") as fh:
            json.dump(book, fh)
        rc, log = run(nb)
        self.assertEqual(rc, 0, log[-2000:])
        result = json.loads(re.search(r"^RESULT (.*)$", log, re.M).group(1))
        if name.startswith(("paren", "func")):
            result = ["" if x is None else x for x in result]                # an empty csv cell reads as null
        self.assertEqual(result, want, name)
        return md

    def test_long_or_chains(self):
        md = self.check("or2000")
        self.assertTrue('df["Cpty"].str.casefold().isin([' in md)
        self.check("or5000")

    def test_long_sum(self):
        md = self.check("plus1500")
        self.assertTrue("functools.reduce(operator.add, [" in md)

    def test_elseif_and_nested_if(self):
        self.check("elseif800")
        self.check("nestedif300")
        self.assertEqual(len(expr.parse(CASES["nestedif300"][1])[1]), 300)       # one IF with 300 tests

    def test_nested_brackets_and_functions(self):
        for name in ("paren400", "paren1000", "func200"):
            self.check(name)

    def test_too_deep_for_python_is_one_step_not_converted(self):
        """A function nested 3000 deep: that tool alone is not converted; the rest of the workflow is."""
        wb = WB()
        wb.text_input(1, ["S"], [["x"]])
        wb.formula(2, [("Deep", "Trim(" * 3000 + "[S]" + ")" * 3000)])
        wb.formula(3, [("Fine", "[S] + 'y'")])
        wb.conn(1, 2)
        wb.conn(2, 3)
        d = tempfile.mkdtemp(prefix="deep_")
        path = os.path.join(d, "wf.yxmd")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(wb.xml())
        from ayx2py import __main__ as cli
        out = os.path.join(d, "out")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.run([path, out]), 0)
        self.assertTrue("1 tool not converted because a rule nests too deeply" in buf.getvalue(), buf.getvalue())
        self.assertTrue("#2 (3000 levels)" in buf.getvalue(), buf.getvalue())
        wf = workflow.Workflow(path)
        self.assertEqual(wf.tools["3"].status, "exact")


if __name__ == "__main__":
    unittest.main()
