"""VBA: parser, specification, generated openpyxl code run on synthetic workbooks, formula evaluator,
and reading VBA out of a workbook file without Excel."""
import datetime
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

import openpyxl

from helpers import HERE, ROOT
import ole_writer
import logicdoc
import xl_runtime as xl
from vba2py import analyse, ovba, transpile, vbaparse

FIXTURE = os.path.join(HERE, "fixtures", "config_macros.bas")
_n = [0]


def load(text, name="M"):
    modules = [vbaparse.parse_module(part, n) for n, part in vbaparse.split_modules(text, name)]
    code, report = transpile.generate(modules, "test")
    tmp = tempfile.mkdtemp(prefix="vba2py_test_")
    _n[0] += 1
    path = os.path.join(tmp, "gen_%d.py" % _n[0])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(code) + "\n")
    spec = importlib.util.spec_from_file_location("gen_%d" % _n[0], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, modules, report, logicdoc.to_markdown(analyse.build(modules, report, "test")).splitlines()


def col(n):
    return openpyxl.utils.get_column_letter(n)


def books(tmp):
    """Synthetic Configuration workbook, a tracker, a trade extract and a lifecycle extract."""
    cfg = openpyxl.Workbook()
    f = cfg.active
    f.title = "Format"
    f.append(["T-1 Sheet", "Tracker A", "Tracker B", "Tracker C", "Tracker D", "s1", "s2", "s3", "s4"])
    f.append(["17", os.path.join(tmp, "tracker_a.xlsx"), None, os.path.join(tmp, "missing.xlsx"), None, "old", "old", "old", "old"])
    imp = cfg.create_sheet("Import")
    imp["A2"], imp["B2"] = os.path.join(tmp, "trades_a.xlsx"), "Sheet1"
    imp["I2"], imp["J2"], imp["O2"], imp["S2"] = os.path.join(tmp, "lifecycle.xlsx"), "LC", "stale", "stale"
    sg = cfg.create_sheet("TradesA")
    sg["A1"], sg["C3"], sg["C4"], sg["C9"] = "Comments", "old", "old2", "old3"
    cfg.create_sheet("Lifecycle")["C3"] = "old"
    look = cfg.create_sheet("LOOKUP_A")
    look.append(["x", "y", "BR", "z", "KEY_ID"])
    look.append([None, None, "BR-0001234567", None, "CI001"])
    look.append([None, None, "XY7654321", None, "CI002"])
    for name in ("Configuration", "Stats", "MailScan"):
        cfg.create_sheet(name)
    cfg.save(os.path.join(tmp, "Configuration.xlsx"))

    tr = openpyxl.Workbook()
    ws = tr.active
    ws.title = "17"
    ws.append(["Comments", "Comments2", "Trade Date"])
    ws.append(["HOLD", None, "x"]); ws.append(["NO ACTION REQUIRED - dup", "MANUAL", "a longer piece of text"])
    tr.create_sheet("16")
    tr.save(os.path.join(tmp, "tracker_a.xlsx"))

    src = openpyxl.Workbook()
    ws = src.active
    ws.title = "Sheet1"
    ws.append(["H%d" % i for i in range(1, 45)])
    for r, party in enumerate(["CI001", "ci002", "NOPE"], 2):
        ws.append(["r%dc%d" % (r, c) if c != 5 else party for c in range(1, 45)])
    src.save(os.path.join(tmp, "trades_a.xlsx"))

    lc = openpyxl.Workbook()
    ws = lc.active
    ws.title = "LC"
    ws.append(["L%d" % i for i in range(1, 39)])
    for r in (2, 3):
        row = ["l%dc%d" % (r, c) for c in range(1, 39)]
        row[27], row[33], row[29], row[28] = 1000 * (r - 1), ("USD", "CHF")[r - 2], 2000 * (r - 1), ("EUR", "GBP")[r - 2]
        ws.append(row)
    lc.save(os.path.join(tmp, "lifecycle.xlsx"))
    return os.path.join(tmp, "Configuration.xlsx")


class Parse(unittest.TestCase):
    def test_lines_statements_and_expressions(self):
        text = 'Sub A()\n x = "a:b" & _\n   "c" \' note\n If x = 1 Then y = 2 Else y = 3: z = 4\n Foo 1, b:=2\nEnd Sub\n'
        m = vbaparse.parse_module(text)
        body = m["procs"][0]["body"]
        self.assertEqual([s["k"] for s in body], ["assign", "if", "call"])
        self.assertEqual(body[0]["comment"], "note")
        self.assertEqual(vbaparse.to_text(body[0]["value"]), '"a:b" & "c"')
        self.assertEqual([s["k"] for s in body[1]["orelse"]], ["assign", "assign"])
        self.assertEqual(body[2]["args"][1][0], "b")
        self.assertEqual(vbaparse.to_text(vbaparse.parse_expr("Not a = 1 And b <> 2 Or c Mod 2 = 0")),
                         "Not a = 1 And b <> 2 Or c Mod 2 = 0")
        e = vbaparse.parse_expr("1 + 2 * 3 & 4")
        self.assertEqual((e[1], e[2][1]), ("&", "+"))                 # & binds looser than +, * tighter

    def test_fixture_shape(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            mods = [vbaparse.parse_module(t, n) for n, t in vbaparse.split_modules(fh.read())]
        self.assertEqual([m["name"] for m in mods], ["Module1", "Module3", "Module5"])
        self.assertEqual([p["name"] for p in mods[1]["procs"]], ["ImportAll", "PurgeAllImport", "ImportTradesA",
                                                                 "ImportLifecycle", "UpdateLookupColumns", "CountFilled"])


class Macros(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.mod, cls.modules, cls.report, cls.spec = load(fh.read())
        cls.text = "\n".join(cls.spec)

    def app(self, save=True):
        tmp = tempfile.mkdtemp(prefix="vba_books_")
        return xl.App(books(tmp), save=save), tmp

    def test_format_all_conditional_formats_and_status(self):
        app, tmp = self.app()
        self.mod.FormatAll(app)
        fmt = app.ThisWorkbook.Sheets("Format")
        self.assertEqual(fmt.Range("F2").Value, "Successfully Formatted")
        self.assertTrue(fmt.Range("H2").Value.startswith("Fail - Sorry, we couldn't find"))     # missing HK file
        self.assertEqual([fmt.Range(c).Value for c in ("G2", "I2")], [None, None])               # F2:I2 cleared first
        self.assertEqual(app.ActiveWorkbook, app.ThisWorkbook)
        ws = openpyxl.load_workbook(os.path.join(tmp, "tracker_a.xlsx"))["17"]
        got = {}
        for cf in ws.conditional_formatting:
            for r in cf.rules:
                c = r.dxf.fill.bgColor
                got[r.priority] = (str(cf.sqref), r.formula[0], bool(r.stopIfTrue), c.rgb if c.type == "rgb" else (c.theme, round(c.tint, 6)))
        self.assertEqual(got, {
            1: ("B2:AS5000", 'B2="MANUAL"', False, "FFFF7C80"),
            2: ("A2:AS5000", '$A2="HOLD"', False, (0, -0.149968)),
            3: ("A2:AS5000", '$B2="MANUAL"', False, "FFFF7C80"),
            4: ("A2:AS5000", 'COUNTIF($A2,"*NO ACTION REQUIRED*")', False, "FFFFFF00"),
        })
        self.assertTrue(any("AutoFit" in line and "estimated" in line for line in app.log))

    def test_save_false_writes_nothing(self):
        app, tmp = self.app(save=False)
        before = os.path.getmtime(os.path.join(tmp, "tracker_a.xlsx")), os.path.getsize(os.path.join(tmp, "tracker_a.xlsx"))
        self.mod.FormatTradesA(app)
        self.assertEqual(before, (os.path.getmtime(os.path.join(tmp, "tracker_a.xlsx")), os.path.getsize(os.path.join(tmp, "tracker_a.xlsx"))))
        self.assertTrue(any(line.startswith("would save") for line in app.log))

    def test_import_all(self):
        app, tmp = self.app()
        self.mod.ImportAll(app)
        sg = app.ThisWorkbook.Sheets("TradesA")
        v = lambda a: sg.Range(a).Value
        self.assertEqual([v("A3"), v("B3"), v("C3"), v("D3"), v("AS3"), v("AT3")], [None, None, "r2c1", "r2c2", "r2c43", None])
        self.assertEqual([v("C5"), v("C6"), v("C9")], ["r4c1", None, None])           # 3 rows in; old rows purged
        self.assertEqual(v("A1"), "Comments")                                         # header rows untouched
        # Account = last 7 characters of BR where KEY_ID = CParty (text match ignores case); no match -> empty
        self.assertEqual([v("I3"), v("I4"), v("I5")], ["1234567", "7654321", None])
        self.assertEqual(v("G4"), "ci002")
        imp = app.ThisWorkbook.Sheets("Import")
        self.assertEqual([imp.Range("O2").Value, imp.Range("S2").Value], ["Successfully Imported"] * 2)
        lc = app.ThisWorkbook.Sheets("Lifecycle")
        w = lambda a: lc.Range(a).Value
        self.assertEqual([w("C3"), w("R4"), w("W3"), w("AB4"), w("AE3"), w("AS4")],
                         ["l2c1", "l3c16", "l2c17", "l3c22", "l2c23", "l3c37"])
        # formulas written to row 3 only, then whole columns pasted back: row 4 loses its amounts (as in the macro)
        self.assertEqual([w("AJ3"), w("AJ4"), w("AL3"), w("AL4")], ["1000 USD", None, "2000 EUR", None])
        self.assertEqual([w("AT3"), w("AU3")], [None, None])
        self.assertEqual(self.mod.CountFilled(app, "TradesA"), 3)

    def test_unconverted_statements_raise(self):
        app, _ = self.app()
        with self.assertRaises(xl.NotConverted) as cm:
            self.mod.MailScan(app)
        self.assertTrue("Outlook.Application" in str(cm.exception))
        app.ThisWorkbook.Sheets("Configuration").Range("T2").Value = app.ThisWorkbook.Sheets("Format").Range("B2").Value
        with self.assertRaises(xl.NotConverted) as cm:                  # not swallowed by the macro's own error handler
            self.mod.UpdateStats(app)
        self.assertTrue(".Find" in str(cm.exception))
        self.assertEqual(len(self.report["problems"]), 4)

    def test_specification(self):
        t = self.text
        for expected in (
                "**Python:** 208 of 212 statements converted; 4 not converted",
                "- FormatAll\n  - FormatTradesA\n  - FormatTradesB",
                "1. Clear the contents of Format!F2:I2",
                "1. Read **format_file** from Format!B2",
                "3. Open the workbook {format_file}",
                "4. Fit the column widths of [{format_file}]{format_sheet}!A2:AS5000 to their contents",
                "5. Add 4 conditional format rules:",
                '[{format_file}]{format_sheet}!A2:AS5000: when `=COUNTIF($A2,"*NO ACTION REQUIRED*")` → fill #FFFF00 '
                "(priority 4 at the end) — ‘Format Yellow for NO ACTION REQUIRED’",
                "when `=$A2=\"HOLD\"` → fill Background 1 (white), 15% darker (priority 2 at the end)",
                '- **Reports in:** Format!F2: "Successfully Formatted", or "Fail - " & the error message',
                "#### If any step fails",
                "Same steps as FormatTradesA, except: B2 → D2; F2 → H2.",
                "2. Delete rows 3:20000 of TradesA",
                "   - Copy [{source_file}]{source_sheet}!A2:AQ{intActiveRow} to TradesA!C3 (values, formulas and formats)",
                '5. If [{source_file}]{source_sheet}!A2 <> "":',
                '         =AJ3&" "&AP3   (the same in A1 notation, in AT3)',
                "   - Copy Lifecycle!AT3:AT10000 to Lifecycle!AJ3 (values only)",
                "4. Replace the formulas in {dataSheet}!I3:I{lastRow} by their results",
                "       =RIGHT(XLOOKUP(G3,'{lookupSheet}'!E:E,'{lookupSheet}'!C:C,\"\"),7)",
                "1. Set **lastRow** = the last filled row of column G",
                "1. Start **Outlook.Application** as olApp — another program; not converted to Python",
                "- Module5 line 11: `Set rngDate = Cells.Find("):
            self.assertTrue(expected in t, expected)
        self.assertFalse("<" in t.replace("<>", "").replace("< 3", ""), "angle brackets would hide text on GitHub")
        self.assertLess(len(self.spec), 260)


class Language(unittest.TestCase):
    def test_control_flow_and_functions(self):
        mod, _, report, _ = load('''
Option Compare Text
Const LIMIT = 3
Function Classify(v)
    Dim s As String, i As Long, total As Long
    For i = 1 To LIMIT
        total = total + i * 2
    Next i
    Do While total > 10
        total = total - 4
    Loop
    Select Case v
        Case 1 To 5: s = "low"
        Case Is > 100
            s = "huge"
        Case "x", "y"
            s = "letter"
        Case Else
            s = "other"
    End Select
    If v = "" Then s = "blank"
    Classify = s & "/" & total & "/" & UCase(Left("abcdef", 2)) & Mid("abcdef", 3, 2) & Len(Trim("  q ")) & InStr("abc", "C")
End Function
Sub Resilient()
    On Error Resume Next
    Range("A1").Value = 1 / 0
    Range("A2").Value = "after"
    On Error GoTo 0
End Sub
Sub Unsupported()
    GoTo Skip
Skip:
    Application.WorksheetFunction.Sum 1
End Sub
''')
        tmp = tempfile.mkdtemp()
        wb = openpyxl.Workbook()
        wb.save(os.path.join(tmp, "m.xlsx"))
        app = xl.App(os.path.join(tmp, "m.xlsx"))
        self.assertEqual(mod.Classify(app, 3), "low/8/ABcd13")              # 12 -> 8; InStr ignores case (Compare Text)
        self.assertEqual(mod.Classify(app, 500).split("/")[0], "huge")
        with self.assertRaises(TypeError):                                    # "Y" >= 1 is error 13, Type mismatch, in VBA too
            mod.Classify(app, "Y")
        self.assertEqual(mod.Classify(app, None).split("/")[0], "blank")     # Empty = "" is True in VBA
        mod.Resilient(app)
        self.assertEqual(app.Range("A2").Value, "after")
        self.assertTrue("Division by zero" in app.Err.Description)
        self.assertEqual(len(report["problems"]), 2)
        with self.assertRaises(xl.NotConverted):
            mod.Unsupported(app)

    def test_vba_value_rules(self):
        xl.COMPARE_TEXT[0] = False
        self.assertTrue(xl.eq(None, "") and xl.eq(None, 0) and xl.eq("5", 5) and not xl.eq("a", "A"))
        self.assertEqual(xl.cat("a", 1.0, None, 2.5, True), "a12.5True")
        self.assertEqual((xl.CLng(2.5), xl.CLng(3.5), xl.idiv(7, 2), xl.mod(-7, 2)), (2, 4, 3, -1))
        self.assertEqual(xl.Format(1234.5, "#,##0.00"), "1,234.50")
        self.assertEqual(xl.Format(datetime.datetime(2026, 11, 17), "dd-mmm-yy"), "17-Nov-26")
        with self.assertRaises(xl.NotConverted):
            xl.Format(1, "General Date")
        self.assertTrue(xl.like("AB12", "AB##") and not xl.like("AB1x", "AB##"))


class Formulas(unittest.TestCase):
    def setUp(self):
        self.wb = openpyxl.Workbook()
        self.ws = self.wb.active
        self.ws.title = "Data"
        look = self.wb.create_sheet("LOOK UP")
        for row in (["BR", "ID"], ["BR-0001234567", "CI001"], ["XY7654321", "CI002"], [None, None], ["LATE99", "CI003"]):
            look.append(row)
        self.ws["A1"], self.ws["B1"], self.ws["C1"], self.ws["D1"] = "ci002", 1250.5, None, datetime.datetime(2026, 11, 17)

    def calc(self, formula, row=1, col=5):
        return xl._Calc(self.wb, self.ws, row, col).run(formula)

    def test_lookup_and_text(self):
        self.assertEqual(self.calc("=RIGHT(XLOOKUP(A1,'LOOK UP'!B:B,'LOOK UP'!A:A,\"\"),7)"), "7654321")
        self.assertEqual(self.calc("=XLOOKUP(\"zzz\",'LOOK UP'!B:B,'LOOK UP'!A:A,\"\")"), "")
        self.assertEqual(self.calc("=XLOOKUP(\"zzz\",'LOOK UP'!B:B,'LOOK UP'!A:A)"), xl.NA)
        self.assertEqual(self.calc("=RIGHT(IFERROR(XLOOKUP(\"zzz\",'LOOK UP'!B:B,'LOOK UP'!A:A),XLOOKUP(\"CI003\",'LOOK UP'!B:B,'LOOK UP'!A:A)),4)"), "TE99")
        self.assertEqual(self.calc("=XLOOKUP(C1,'LOOK UP'!B:B,'LOOK UP'!A:A,\"none\")"), None)       # blank key finds the blank row
        self.assertEqual(self.calc("=VLOOKUP(\"CI001\",'LOOK UP'!B2:B5,1,FALSE)"), "CI001")
        self.assertEqual(self.calc("=INDEX('LOOK UP'!A:A,MATCH(\"ci003\",'LOOK UP'!B:B,0))"), "LATE99")
        self.assertEqual(self.calc('=B1&" "&A1'), "1250.5 ci002")
        self.assertEqual(self.calc('=D1&""'), "46343")                                           # a date is its serial number
        self.assertEqual(self.calc('=IF(A1="CI002",LEN(A1)+1,0)'), 6)
        self.assertEqual(self.calc('=COUNTIF(A1:A1,"*i00*")+SUM(B1:B1,1)'), 1252.5)
        self.assertEqual(self.calc("=1/0"), xl.XlError("#DIV/0!"))

    def test_refuses_what_it_does_not_know(self):
        for f in ("=TEXT(B1,\"0.00\")", "=VLOOKUP(1,A1:B1,2)", "=SUMPRODUCT(A1:A2)", "=MyName+1"):
            with self.assertRaises(xl.NotConverted, msg=f):
                self.calc(f)

    def test_r1c1(self):
        self.assertEqual(xl.r1c1_to_a1('=RC[-10]&" "&RC[-4]', 3, 46), '=AJ3&" "&AP3')
        self.assertEqual(xl.r1c1_to_a1("=R1C1+R[1]C+RC2&\"RC[1]\"", 5, 3), '=$A$1+C6+$B5&"RC[1]"')

    def test_end_and_cached_values(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "b.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        for r, v in enumerate(["h", "a", "b", None, "c"], 1):
            ws.cell(r, 1).value = v
        ws["B1"] = '=TEXT(1,"0")'                                    # not in the evaluator's list
        wb.save(path)
        app = xl.App(path)
        self.assertEqual(app.Range("A50000").End(xl.xlUp).Row, 5)
        self.assertEqual(app.Cells(app.Rows.Count, "a").End(xl.xlUp).Row, 5)
        self.assertEqual(app.Range("A1").End(xl.xlDown).Row, 3)
        self.assertEqual(app.Range("A3").End(xl.xlDown).Row, 5)
        self.assertEqual(app.Range("B50000").End(xl.xlUp).Row, 1)
        with self.assertRaises(xl.NotConverted):                     # no cached result (file never calculated by Excel)
            app.Range("B1").Value


class ReadWorkbook(unittest.TestCase):
    def test_compression_matches_the_specification_examples(self):
        cases = [("01 19 B0 00 61 62 63 64 65 66 67 68 00 69 6A 6B 6C 6D 6E 6F 70 00 71 72 73 74 75 76 2E", b"abcdefghijklmnopqrstuv."),
                 ("01 2F B0 00 23 61 61 61 62 63 64 65 82 66 00 70 61 67 68 69 6A 01 38 08 61 6B 6C 00 30 6D 6E 6F 70 06 71 02 "
                  "70 04 10 72 73 74 75 76 10 77 78 79 7A 00 3C", b"#aaabcdefaaaaghijaaaaaklaaamnopqaaaaaaaaaaaarstuvwxyzaaa"),
                 ("01 03 B0 02 61 45 00", b"a" * 73)]
        for hexa, plain in cases:
            self.assertEqual(ovba.decompress(bytes.fromhex(hexa.replace(" ", ""))), plain)
        self.assertEqual(ole_writer.compress(b"a" * 73), bytes.fromhex("0103B002614500"))
        for _, plain in cases:                                        # the test's own writer round-trips
            self.assertEqual(ovba.decompress(ole_writer.compress(plain * 200)), plain * 200)

    def test_modules_read_back_from_an_xlsm(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            parts = [(m, t.replace("\n", "\r\n")) for m, t in zip(["Module1", "Module3", "Module5"],
                                                                  [p for _, p in vbaparse.split_modules(fh.read())])]
        parts.append(("ThisWorkbook", "Private Sub Workbook_Open()\r\n    MsgBox \"héllo\"\r\nEnd Sub\r\n"))
        self.assertGreater(len(parts[0][1]), 4096)                    # more than one compressed chunk
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "Configuration.xlsm")
        ole_writer.write_xlsm(path, parts)
        self.assertEqual(ovba.read_vba(path), parts)
        r = subprocess.run([sys.executable, "-m", "vba2py", path, "-o", os.path.join(tmp, "out")], cwd=ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(sorted(os.listdir(os.path.join(tmp, "out"))),
                         ["Configuration_generated.py", "Configuration_logic.html", "Configuration_logic.md",
                          "Configuration_vba_source.txt", "xl_runtime.py"])
        with open(os.path.join(tmp, "out", "Configuration_logic.md"), encoding="utf-8") as fh:
            self.assertTrue("Same steps as FormatTradesA, except: B2 → D2; F2 → H2." in fh.read())

    def test_bad_files_are_refused(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "x.xlsm")
        with open(p, "wb") as fh:
            fh.write(b"not a workbook")
        with self.assertRaises(ovba.OVBAError):
            ovba.read_vba(p)


if __name__ == "__main__":
    unittest.main()
