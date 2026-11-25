"""Rules with thousands of terms or thousands of nesting levels: parsed, described in the guide, generated as Python
that compiles and computes the right values, and given pandas lines that run when pasted into the steps notebook.
Everything runs on the main thread with Python's default recursion limit: nothing needs a deeper stack."""
import json
import os
import re
import sys
import tempfile
import unittest

import pandas as pd

from helpers import convert, rt
from ayx2py import codegen, expr, guide, workflow  # noqa: F401
from test_steps_notebook import fill, generate, run
from yx import WB

N = 1500


def mixed(pairs):
    """[P] = "a0" AND [Q] = "b0" OR [P] = "a1" AND [Q] = "b1" ...: Alteryx reads it left to right."""
    return " OR ".join('[P] = "a%d" AND [Q] = "b%d"' % (i, i) for i in range(pairs))


def mixed_keeps(pairs, p, q):
    v = p.casefold() == "a0"
    for i in range(pairs):
        if i:
            v = v or p.casefold() == "a%d" % i
        v = v and q.casefold() == "b%d" % i
    return v


PQ = [("a5", "B5"), ("a5", "x"), ("A%d", "b%d"), ("z", "b0")]
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
    "func3000": ("Formula", "Trim(" * 3000 + "[S]" + ")" * 3000, "Out", ["ab", "cd", "", "e"]),
    "plusminus2000": ("Formula", " + ".join("[F%d] - 1" % i for i in range(1000)), "Out", [0.0] * 4),
    "andor_mixed3000": ("Filter", mixed(1500), None, None),
    "mixed20000": ("Filter", mixed(10000), None, None),
}
DATA = pd.DataFrame({"Cpty": ["C5", "c1999", "X", "C4999"], "A": [1.0, 5.0, 799.0, 900.0], "S": [" ab ", "cd", "", " e"],
                     **{"F%d" % i: [1.0] * 4 for i in range(N)}})
DATA.loc[2, "S"] = ""
DATA["P"] = [p for p, _ in PQ]
DATA["Q"] = [q for _, q in PQ]
ROWS_FOR = {"nestedif300": [1.0, 5.0, 299.0, 400.0]}


def workflow_for(name):
    tool, rule, out, _ = CASES[name]
    wb = WB()
    types = {"Cpty": "V_String", "A": "Double", "S": "V_String", "P": "V_String", "Q": "V_String",
             **{"F%d" % i: "Double" for i in range(N)}}
    wb.input(1, "Input\\data.csv", types, CodePage=65001, Delimeter=",", HeaderRow="True")
    if tool == "Filter":
        wb.filter(2, rule)
    else:
        wb.formula(2, [(out, rule, "Double" if name in ("plus1500", "plusminus2000") else "V_WString")])
    wb.output(3, "Output\\out.csv")
    wb.conn(1, 2)
    wb.conn(2, 3, sa="True" if tool == "Filter" else "Output")
    return wb


def data_for(name):
    df = DATA.copy()
    if name in ROWS_FOR:
        df["A"] = ROWS_FOR[name]
    if name.startswith(("andor", "mixed")):
        pairs = 1500 if name.startswith("andor") else 10000
        df["P"] = [p % (pairs - 1) if "%" in p else p for p, _ in PQ]
        df["Q"] = [q % (pairs - 1) if "%" in q else q for _, q in PQ]
    return df


def want_for(name):
    tool, _, _, want = CASES[name]
    if want is not None:
        return want
    pairs = 1500 if name.startswith("andor") else 10000
    df = data_for(name)
    return [c for c, p, q in zip(df["Cpty"], df["P"], df["Q"]) if mixed_keeps(pairs, p, q)]


class Deep(unittest.TestCase):
    def setUp(self):
        self.limit = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)                        # Python's default: no deeper stack is needed

    def tearDown(self):
        sys.setrecursionlimit(self.limit)

    def check(self, name):
        tool, rule, out, _ = CASES[name]
        want = want_for(name)
        node = expr.parse(rule)
        if not name.startswith(("paren", "func", "andor", "mixed", "plusminus")):
            self.assertLessEqual(expr.depth(node), 3, name)
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
        for name in ("paren400", "paren1000", "func200", "func3000"):
            self.check(name)

    def test_left_to_right_mixed_chains(self):
        """AND/OR and +/- alternating: one level per term, read left to right as Alteryx does."""
        md = self.check("andor_mixed3000")
        self.assertTrue("(read top down: each line combines with everything above it)" in md)
        self.assertTrue('      or [P] = "a1"\n      and [Q] = "b1"\n' in md)
        self.assertTrue("t1 = t1 | eq(df[\"P\"], 'a1')" in md)
        self.check("plusminus2000")
        self.check("mixed20000")
        self.assertTrue(want_for("mixed20000"))                       # the test data does reach the end of the chain

    def test_a_step_without_python_is_still_described_in_full(self):
        """If the Python for a step cannot be made, only its Python line says so: the step, what it does, its columns
        and its whole rule are still in the guide."""
        wb = WB()
        wb.text_input(1, ["P", "Q"], [["a1", "b1"]])
        wb.formula(2, [("Hit", 'IF %s THEN "yes" ELSE "no" ENDIF' % mixed(20))])
        wb.conn(1, 2)
        d = tempfile.mkdtemp(prefix="deep_")
        path = os.path.join(d, "wf.yxmd")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(wb.xml())
        real = codegen.tool_code

        def failing(t, name):
            if t.id == "2":
                raise RecursionError("simulated")
            return real(t, name)

        codegen.tool_code = failing
        try:
            wf = workflow.Workflow(path)
            g = guide.Guide(wf)
            import logicdoc
            md = logicdoc.to_markdown(g.build())
        finally:
            codegen.tool_code = real
        self.assertEqual(wf.tools["2"].status, "stub")
        self.assertTrue(re.search(r"^\| 1\.2 \| Formula ⚠ \| fills column \*\*Hit\*\* \(see the rule\) \| \*\*Hit\*\* \| ends \(rows dropped\) \|$",
                                  md, re.M), md[:3000])
        fold = md[md.index("- **1.2** Formula #2"):]
        self.assertTrue("**not converted**: the generated Python does not compile: RecursionError" in fold.split("\n")[0])
        self.assertTrue('      when (read top down: each line combines with everything above it)' in fold or
                        '[P] = "a19"' in fold, fold[:800])
        self.assertEqual(fold.count('"a19"'), 1)
        self.assertTrue("np.select" in fold)                      # the pandas form is still given


if __name__ == "__main__":
    unittest.main()
