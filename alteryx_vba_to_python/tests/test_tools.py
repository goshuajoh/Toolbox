"""End to end, one tool kind at a time: synthetic .yxmd -> generated Python -> frames compared with
hand-computed expectations."""
import os
import tempfile
import unittest

from helpers import convert, rt, table
from yx import WB

S, D, I = "V_WString", "Double", "Int64"


def run(wb, **ctx):
    wf, mod, lines, info = convert(wb)
    c = rt.Context(**ctx)
    return mod.run(c), c, wf, lines


def source(wb, tid, columns, rows, types):
    return wb.text_input(tid, columns, rows, dict(zip(columns, types)))


class Tools(unittest.TestCase):
    def test_text_input_select_formula(self):
        wb = WB()
        source(wb, 1, ["Deal Id", "Long/Short", "Amt", "Junk"], [["A1", "Long", "10", "x"], ["A2", "short", "2.5", "y"]], [S, S, S, S])
        wb.select(2, [("Deal Id", True, "DEAL ID"), ("Amt", True, None, "Double"), ("Junk", False), ("*Unknown", True)])
        wb.formula(3, [("SIDE", "If [Long/Short]='Long' then 'BUY' elseif [Long/Short]='Short' then 'SELL' else '' endif"),
                       ("Twice", "[Amt] * 2", "Double"), ("Label", "[SIDE] + '-' + ToString([Twice])")])
        wb.conn(1, 2); wb.conn(2, 3)
        s, *_ = run(wb)
        self.assertEqual(list(s["3"].columns), ["DEAL ID", "Amt", "Long/Short", "SIDE", "Twice", "Label"])
        self.assertEqual(table(s["3"]), [("A1", 10.0, "Long", "BUY", 20.0, "BUY-20"), ("A2", 2.5, "short", "SELL", 5.0, "SELL-5")])
        self.assertEqual(rt.types_of(s["3"])["Amt"], "Double")

    def test_select_keeps_only_and_reorders(self):
        wb = WB()
        source(wb, 1, ["A", "B", "C"], [["1", "2", "3"]], [S, S, S])
        wb.select(2, [("C", True), ("A", True, "First"), ("*Unknown", False)])
        wb.conn(1, 2)
        s, *_ = run(wb)
        self.assertEqual(list(s["2"].columns), ["C", "First"])

    def test_filter_custom_and_basic(self):
        wb = WB()
        source(wb, 1, ["SYSTEM", "N", "K"], [["SYSA", "1", "x"], ["sysb", "2", None], ["SysA", "3", ""]], [S, I, S])
        wb.filter(2, simple=("SYSTEM", "=", "SYSA"))
        wb.filter(3, "[N] >= 2 AND !IsEmpty([K])")
        wb.filter(4, simple=("K", "IsNotNull", ""))
        wb.filter(5, simple=("N", ">", "1"))
        for t in (2, 3, 4, 5):
            wb.conn(1, t)
        blank = rt.frame(["SYSTEM", "N", "K"], [["SYSA", 1, "x"], ["sysb", 2, None], ["SysA", 3, ""]])
        s, *_ = run(wb, inputs={"1": blank})                                    # a typed table cannot hold ''
        self.assertEqual([r[1] for r in table(s["2"]["True"])], [1, 3])          # = ignores case
        self.assertEqual([r[1] for r in table(s["2"]["False"])], [2])
        self.assertEqual(table(s["3"]["True"]), [])
        self.assertEqual([r[1] for r in table(s["4"]["True"])], [1, 3])          # '' is not null
        self.assertEqual([r[1] for r in table(s["5"]["True"])], [2, 3])          # operand read as a number

    def test_join_outputs_rename_and_case(self):
        wb = WB()
        source(wb, 1, ["DEAL ID", "AMOUNT1"], [["D2", "5"], ["D1", "7"], ["d1", "8"], [None, "9"]], [S, D])
        source(wb, 2, ["DEAL ID", "AMOUNT1", "RISK"], [["D1", "70", "TYPEA"], ["D3", "1", "X"], ["D1", "71", "TYPEC"]], [S, D, S])
        wb.join(3, ["DEAL ID"], ["DEAL ID"], [("Right_DEAL ID", True, "SYSB_DEAL ID"), ("Right_AMOUNT1", True, "SYSB_AMOUNT1"),
                                              ("*Unknown", True)])
        wb.conn(1, 3, da="Left"); wb.conn(2, 3, da="Right")
        s, *_ = run(wb)
        j = s["3"]
        self.assertEqual(list(j["Join"].columns), ["SYSB_DEAL ID", "SYSB_AMOUNT1", "DEAL ID", "AMOUNT1", "RISK"])
        self.assertEqual(table(j["Join"]), [("D1", 70.0, "D1", 7.0, "TYPEA"), ("D1", 71.0, "D1", 7.0, "TYPEC")])
        self.assertEqual([r[0] for r in table(j["Left"])], [None, "D2", "d1"])     # keys are case-sensitive; sorted by key
        self.assertEqual(table(j["Right"]), [("D3", 1.0, "X")])

    def test_join_default_names_and_type_check(self):
        wb = WB()
        source(wb, 1, ["K", "V"], [["1", "a"]], [S, S])
        source(wb, 2, ["K", "V", "W"], [["1", "b", "c"]], [S, S, S])
        source(wb, 4, ["K"], [["1"]], [I])
        wb.join(3, ["K"], ["K"])
        wb.join(5, ["K"], ["K"])
        wb.conn(1, 3, da="Left"); wb.conn(2, 3, da="Right"); wb.conn(1, 5, da="Left"); wb.conn(4, 5, da="Right")
        wf, mod, _, _ = convert(wb)
        with self.assertRaises(rt.AlteryxError):                                    # string key joined to a number
            mod.run(rt.Context())
        wb2 = WB()
        source(wb2, 1, ["K", "V"], [["1", "a"]], [S, S])
        source(wb2, 2, ["K", "V", "W"], [["1", "b", "c"]], [S, S, S])
        wb2.join(3, ["K"], ["K"])
        wb2.conn(1, 3, da="Left"); wb2.conn(2, 3, da="Right")
        s, *_ = run(wb2)
        self.assertEqual(list(s["3"]["Join"].columns), ["K", "V", "Right_K", "Right_V", "W"])

    def test_union_by_name_type_coercion_and_order(self):
        wb = WB()
        source(wb, 1, ["ID", "AMT", "ONLY1"], [["a", "1.5", "p"]], [S, D, S])
        source(wb, 2, ["AMT", "ID", "ONLY2"], [["1,250 USD", "b", "q"]], [S, S, S])
        source(wb, 3, ["id", "AMT"], [["c", "100"]], [S, I])
        wb.union(4)
        wb.conn(2, 4, name="#2"); wb.conn(1, 4, name="#1"); wb.conn(3, 4, name="#3")
        s, *_ = run(wb)
        self.assertEqual(list(s["4"].columns), ["ID", "AMT", "ONLY1", "ONLY2"])
        # AMT is Double in #1, String in #2, Int in #3 -> String; numbers are rewritten as text
        self.assertEqual(table(s["4"]), [("a", "1.5", "p", None), ("b", "1,250 USD", None, "q"), ("c", "100", None, None)])
        self.assertEqual(rt.types_of(s["4"])["AMT"], "V_WString")

    def test_union_explicit_order(self):
        wb = WB()
        source(wb, 1, ["N"], [["1"]], [I]); source(wb, 2, ["N"], [["2.5"]], [D])
        wb.union(3, order=["#2", "#1"])
        wb.conn(1, 3, name="#1"); wb.conn(2, 3, name="#2")
        s, *_ = run(wb)
        self.assertEqual(table(s["3"]), [(2.5,), (1.0,)])                            # Int + Double -> Double

    def test_summarize(self):
        wb = WB()
        source(wb, 1, ["DEAL", "AMT", "DT", "TXT"], [["b", "2", "2026-01-02", "x"], ["a", "1", "2026-01-05", ""],
                                                    ["b", None, "2026-01-01", None], ["b", "4", "2026-01-03", "z"]],
               [S, D, "Date", S])
        wb.summarize(2, [("DEAL", "GroupBy", "DEAL"), ("DEAL", "Count", "No. of DEAL ID"), ("AMT", "Sum", "Sum_AMT"),
                         ("AMT", "CountNonNull", "n"), ("DT", "Last", "Last_DT"), ("DT", "Max", "Max_DT"),
                         ("TXT", "Concat", "All", ";")])
        wb.summarize(3, [("AMT", "Sum", "Total"), ("AMT", "Avg", "Mean")])
        wb.conn(1, 2); wb.conn(1, 3)
        s, *_ = run(wb)
        self.assertEqual(table(s["2"]), [("a", 1, 1.0, 1, "2026-01-05", "2026-01-05", ""),
                                         ("b", 3, 6.0, 2, "2026-01-03", "2026-01-03", "x;;z")])   # Last = stream order
        self.assertEqual(table(s["3"]), [(7.0, 7 / 3)])

    def test_sort_unique_sample_record_id(self):
        wb = WB()
        source(wb, 1, ["K", "N"], [["b", "2"], ["a", "9"], [None, "5"], ["B", "1"], ["a", "3"]], [S, I])
        wb.sort(2, [("K", "Ascending"), ("N", "Descending")])
        wb.unique(3, ["K"])
        wb.sample(4, "First", 1, ["K"])
        wb.sample(5, "Skip", 3)
        wb.record_id(6, "RecordID", 1, "String", 6)
        wb.record_id(7, "Row", 10, "Int32")
        for a, b in ((1, 2), (1, 3), (2, 4), (1, 5), (1, 6), (1, 7)):
            wb.conn(a, b)
        s, *_ = run(wb)
        self.assertEqual(table(s["2"]), [(None, 5), ("B", 1), ("a", 9), ("a", 3), ("b", 2)])
        self.assertEqual(table(s["3"]["Unique"]), [("b", 2), ("a", 9), (None, 5), ("B", 1)])
        self.assertEqual(table(s["3"]["Duplicates"]), [("a", 3)])
        self.assertEqual(table(s["4"]), [(None, 5), ("B", 1), ("a", 9), ("b", 2)])
        self.assertEqual(table(s["5"]), [("B", 1), ("a", 3)])
        self.assertEqual([r[0] for r in table(s["6"])], ["000001", "000002", "000003", "000004", "000005"])
        self.assertEqual(list(s["6"].columns), ["RecordID", "K", "N"])
        self.assertEqual([r[0] for r in table(s["7"])], [10, 11, 12, 13, 14])

    def test_transpose_concat_multifield(self):
        wb = WB()
        source(wb, 1, ["Unique ID", "Discrepancy in Side", "Discrepancy in Currency", "Other"],
               [["T1", "", "Discrepancy in Currency", "o"], ["T2", "Discrepancy in Side", "Discrepancy in Currency", "o"]], [S] * 4)
        wb.transpose(2, ["Unique ID"], ["Discrepancy in Side", "Discrepancy in Currency"])
        wb.summarize(3, [("Unique ID", "GroupBy", "Unique ID"), ("Value", "Concat", "Discrepancies", ";")])
        wb.multi_field(4, ["Discrepancies"], r'REGEX_Replace(REGEX_Replace([_CurrentField_],"\G;",""),";$","")')
        wb.conn(1, 2); wb.conn(2, 3); wb.conn(3, 4)
        src = rt.frame(["Unique ID", "Discrepancy in Side", "Discrepancy in Currency", "Other"],
                       [["T1", "", "Discrepancy in Currency", "o"], ["T2", "Discrepancy in Side", "Discrepancy in Currency", "o"]])
        s, *_ = run(wb, inputs={"1": src})
        self.assertEqual(table(s["2"])[:2], [("T1", "Discrepancy in Side", ""), ("T1", "Discrepancy in Currency", "Discrepancy in Currency")])
        self.assertEqual(table(s["3"]), [("T1", ";Discrepancy in Currency"), ("T2", "Discrepancy in Side;Discrepancy in Currency")])
        self.assertEqual(table(s["4"]), [("T1", "Discrepancy in Currency"), ("T2", "Discrepancy in Side;Discrepancy in Currency")])

    def test_transpose_mixed_types_become_text(self):
        wb = WB()
        source(wb, 1, ["K", "A", "B"], [["k", "1.5", "x"]], [S, D, S])
        wb.transpose(2, ["K"], ["A", "B"])
        wb.conn(1, 2)
        s, *_ = run(wb)
        self.assertEqual(table(s["2"]), [("k", "A", "1.5"), ("k", "B", "x")])

    def test_find_replace_append_is_a_substring_join(self):
        wb = WB()
        source(wb, 1, ["DEAL ID", "V"], [["1234", "a"], ["12345", "b"], ["99", "c"], [None, "d"], ["x123", "e"]], [S, S])
        source(wb, 2, ["DEAL ID", "No. of DEAL ID"], [["123", "7"], ["12345", "2"], ["", "0"], ["99", "1"]], [S, I])
        wb.find_replace(3, "DEAL ID", "DEAL ID", "FindAny", ["No. of DEAL ID"])
        wb.find_replace(4, "DEAL ID", "DEAL ID", "FindBegin", ["No. of DEAL ID"])
        wb.find_replace(5, "DEAL ID", "DEAL ID", "FindWhole", ["No. of DEAL ID"])
        for t in (3, 4, 5):
            wb.conn(1, t, da="Targets"); wb.conn(2, t, da="Source")
        s, *_ = run(wb)
        # FindAny: '123' is found inside '1234', '12345' and 'x123': not an equality join.
        self.assertEqual([r[2] for r in table(s["3"])], [7, 7, 1, None, 7])
        self.assertEqual([r[2] for r in table(s["4"])], [7, 7, 1, None, None])
        self.assertEqual([r[2] for r in table(s["5"])], [None, 2, 1, None, None])

    def test_find_replace_case_and_replace_mode(self):
        wb = WB()
        source(wb, 1, ["CCY"], [["CNH"], ["cnh"], ["USD/CNH"]], [S])
        source(wb, 2, ["From", "To"], [["CNH", "CNY"]], [S, S])
        wb.find_replace(3, "CCY", "From", "FindAny", replace="To")
        wb.find_replace(4, "CCY", "From", "FindAny", replace="To", nocase=True)
        for t in (3, 4):
            wb.conn(1, t, da="Targets"); wb.conn(2, t, da="Source")
        s, *_ = run(wb)
        self.assertEqual([r[0] for r in table(s["3"])], ["CNY", "cnh", "USD/CNY"])
        self.assertEqual([r[0] for r in table(s["4"])], ["CNY", "CNY", "USD/CNY"])

    def test_append_fields(self):
        wb = WB()
        source(wb, 1, ["A", "T"], [["1", "x"], ["2", "y"]], [S, S])
        source(wb, 2, ["T", "Cfg"], [["2026-11-17", "c"]], ["Date", S])
        wb.append_fields(3, [("Source_T", True, "RunDate"), ("*Unknown", True)])
        wb.conn(1, 3, da="Targets"); wb.conn(2, 3, da="Source")
        s, *_ = run(wb)
        self.assertEqual(list(s["3"].columns), ["RunDate", "A", "T", "Cfg"])
        self.assertEqual(table(s["3"]), [("2026-11-17", "1", "x", "c"), ("2026-11-17", "2", "y", "c")])

    def test_datetime_tool(self):
        wb = WB()
        source(wb, 1, ["E"], [["17/11/2026"], ["1/2/2026"], ["nonsense"], [None]], [S])
        wb.datetime(2, "E", "dd/MM/yyyy", "EXPIRY DATE")
        wb.conn(1, 2)
        s, *_ = run(wb)
        self.assertEqual([r[1] for r in table(s["2"])], ["2026-11-17", "2026-02-01", None, None])
        self.assertEqual(rt.types_of(s["2"])["EXPIRY DATE"], "Date")

    def test_cleanse(self):
        wb = WB()
        source(wb, 1, ["A", "B", "N"], [["  x  y ", " keep ", None], [None, None, "3"]], [S, S, D])
        wb.cleanse(2, ["A", "N"], box_84="True", box_117="True", box_109="True")
        wb.conn(1, 2)
        s, _, wf, _ = run(wb)
        self.assertEqual(table(s["2"]), [("x y", " keep ", 0), ("", None, 3.0)])
        self.assertEqual(wf.tools["2"].status, "caveat")

    def test_csv_xlsx_directory_dynamic_input_output(self):
        import openpyxl
        tmp = tempfile.mkdtemp(prefix="ayx_io_")
        os.makedirs(os.path.join(tmp, "Out"))
        with open(os.path.join(tmp, "FX_20261117.csv"), "w", encoding="latin-1", newline="") as fh:
            fh.write('DEALID,AMT,NOTE,AMT\r\n"00123",1250.50,"a, b",\r\nX9,,é,7\r\n')
        wb_ = openpyxl.Workbook()
        ws = wb_.active
        ws.title = "Configuration"
        import datetime
        ws.append(["T", "Branch", "Mixed"]); ws.append([datetime.datetime(2026, 11, 17), 101, 1]); ws.append([None, 102, "x"])
        wb_.save(os.path.join(tmp, "Configuration.xlsx"))
        wb = WB()
        wb.input(1, "Configuration.xlsx|||`Configuration$`", {"T": "Date", "Branch": "V_String", "Mixed": "V_String"})
        wb.formula(2, [("File", "'FX_'+replace([T],'-','')+'.csv'")])
        wb.filter(3, simple=("T", "IsNotNull", ""))
        wb.dynamic_input(4, "FX_template.csv", "File", CodePage=28591, Delimeter=",", HeaderRow="True", FieldLen=254)
        wb.output(5, "Out\\result.xlsx|||Reconciliation", OutputOption="Overwrite")
        wb.output(6, "Out\\result.csv")
        wb.directory(7, "Out")
        wb.conn(1, 2); wb.conn(2, 3); wb.conn(3, 4, sa="True"); wb.conn(4, 5); wb.conn(4, 6)
        s, ctx, _, _ = run(wb, workflow_dir=tmp)
        self.assertEqual(table(s["1"]), [("2026-11-17", "101", "1"), (None, "102", "x")])   # recorded types applied
        self.assertEqual(list(s["4"].columns), ["DEALID", "AMT", "NOTE", "AMT2"])           # duplicate header renamed
        self.assertEqual(table(s["4"]), [("00123", "1250.50", "a, b", None), ("X9", None, "é", "7")])
        self.assertEqual(os.listdir(os.path.join(tmp, "Out")), [])                          # write=False writes nothing
        self.assertEqual(sorted(ctx.outputs), ["5", "6"])
        s, ctx, _, _ = run(wb, workflow_dir=tmp, write=True)
        self.assertEqual(sorted(os.listdir(os.path.join(tmp, "Out"))), ["result.csv", "result.xlsx"])
        back = openpyxl.load_workbook(os.path.join(tmp, "Out", "result.xlsx"))["Reconciliation"]
        self.assertEqual([c.value for c in back[2]], ["00123", "1250.50", "a, b", None])
        s, ctx, _, _ = run(wb, workflow_dir=tmp, inputs={"7": rt.frame(["FileName"], [["a.xlsx"]])})
        self.assertEqual(table(s["7"]), [("a.xlsx",)])

    def test_stubs_raise_and_can_be_replaced(self):
        wb = WB()
        source(wb, 1, ["A"], [["1"]], [S])
        wb.run_command(2, "cscript.exe", "RunMacro.vbs ImportAll")
        wb.formula(3, [("B", "SomeUnknownFunction([A])")])
        wb.raw(4, "MultiRowFormula", "<Expression>[Row-1:A]</Expression>")
        wb.sample(5, "NPercent", 10)
        wb.conn(1, 2); wb.conn(1, 3); wb.conn(1, 4); wb.conn(1, 5)
        wf, mod, lines, _ = convert(wb)
        self.assertEqual({i: wf.tools[i].status for i in "2345"}, {"2": "caveat", "3": "stub", "4": "stub", "5": "stub"})
        ctx = rt.Context()
        with self.assertRaises(rt.NotConverted) as cm:
            mod.run(ctx)
        self.assertIn("#3", str(cm.exception))
        self.assertEqual(ctx.log[0], "#2 external program not run: cscript.exe RunMacro.vbs ImportAll")  # logged only
        text = "\n".join(lines)
        self.assertIn("NOT CONVERTED", text)
        self.assertIn("RunMacro.vbs ImportAll", text)
        self.assertIn("SomeUnknownFunction", text)
        done = mod.run(rt.Context(stubs={i: (lambda *inputs: inputs[0] if inputs else None) for i in "2345"}))
        self.assertEqual(table(done["3"]), [("1",)])

    def test_disabled_container_browse_and_block_until_done(self):
        wb = WB()
        wb.container(["Main"], 100)
        wb.container(["Main", "Old logic"], 101, disabled=True)
        source(wb, 1, ["A"], [["1"]], [S])
        wb.formula(2, [("B", "'off'")], box=("Main", "Old logic"))
        wb.formula(3, [("B", "'on'")], box=("Main",))
        wb.union(4, box=("Main",))
        wb.browse(5); wb.text_box(6, "Remember to refresh the extract")
        wb.pbud(7)
        wb.output(8, "first.csv"); wb.output(9, "second.csv")
        wb.message(10, "Filter", "Error", '"bad row " + [A]', "[A] = 'never'")
        wb.conn(1, 2); wb.conn(1, 3); wb.conn(2, 4, name="#1"); wb.conn(3, 4, name="#2"); wb.conn(4, 5)
        wb.conn(4, 10); wb.conn(10, 7, da="Input2"); wb.conn(3, 7, da="Input1")
        wb.conn(7, 9, sa="Output2"); wb.conn(7, 8, sa="Output1")
        s, ctx, wf, lines = run(wb)
        self.assertNotIn("2", s)                                                   # disabled: not run
        self.assertEqual(table(s["4"]), [("1", "on")])                            # the union just loses that input
        self.assertLess(wf.rank["8"], wf.rank["9"])                               # output 1 finishes before output 2
        self.assertEqual(wf.order_edges, [("8", "9")])
        text = "\n".join(lines)
        self.assertIn("DISABLED CONTAINERS", text)
        self.assertIn("Old logic: 1 tools #2", text)
        self.assertIn("#8→#9", text)

    def test_message_error_stops(self):
        wb = WB()
        source(wb, 1, ["A"], [["x"]], [S])
        wb.message(2, "Filter", "Error", '"bad row " + [A]', "[A] = 'X'")
        wb.conn(1, 2)
        wf, mod, _, _ = convert(wb)
        with self.assertRaises(rt.AlteryxError) as cm:
            mod.run(rt.Context())
        self.assertIn("bad row x", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
