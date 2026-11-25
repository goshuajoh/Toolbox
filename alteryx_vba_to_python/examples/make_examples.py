"""Builds the example inputs and their outputs in this folder:

    dataset_audit.yxmd      an Alteryx workflow laid out the way Designer users often build them: one
                            input fanning out into six annotated filter branches inside a nested
                            container, merging into one Union; a second container writes the file it
                            reads; a disabled container                     -> dataset_audit_guide.md / .html
    sales_report.yxmd       a small Alteryx workflow (monthly sales summary)   -> sales_report_guide.md / .html
    month_end.bas           a small VBA module (month-end workbook routine)    -> month_end_logic.md / .html

Neither has anything to do with the workflow and macros the tools were first written for; they show
that the tools read any workflow or macro. All names and values are invented.

    python examples/make_examples.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import logicdoc                                   # noqa: E402
from ayx2py import guide, notebook, workflow      # noqa: E402
from vba2py import analyse, transpile, vbaparse   # noqa: E402
from yx import WB                                 # noqa: E402

S, D, I = "V_String", "Double", "Int32"

VBA = '''Attribute VB_Name = "MonthEnd"
Option Explicit

' Month-end routine: bring in the ledger extract, total it, flag problems, save a dated copy.
Sub MonthEnd()
    Call ImportLedger
    Call AddTotals
    Call FlagExceptions
    Call SaveDatedCopy
End Sub

Sub ImportLedger()
    Dim wbSrc As Workbook, wbMain As Workbook
    Dim lastRow As Long
    On Error GoTo Failed
    Set wbMain = ThisWorkbook
    Sheets("Control").Activate
    srcPath = Range("B3").Value
    Workbooks.Open Filename:=srcPath, ReadOnly:=True
    Set wbSrc = ActiveWorkbook
    Sheets("Ledger").Activate
    lastRow = Cells(Rows.Count, 1).End(xlUp).Row
    wbMain.Sheets("Data").Range("A2:H20000").ClearContents
    Range("A2:H" & lastRow).Copy
    wbMain.Sheets("Data").Range("A2").PasteSpecial Paste:=xlPasteValues
    wbSrc.Close SaveChanges:=False
    wbMain.Sheets("Control").Range("C3").Value = "Imported " & (lastRow - 1) & " rows"
    Exit Sub
Failed:
    ThisWorkbook.Sheets("Control").Range("C3").Value = "Import failed: " & Err.Description
End Sub

Sub AddTotals()
    Dim ws As Worksheet, lastRow As Long
    Set ws = ThisWorkbook.Sheets("Data")
    lastRow = ws.Cells(ws.Rows.Count, "A").End(xlUp).Row
    With ws.Range("I2:I" & lastRow)
        .FormulaR1C1 = "=IF(RC[-3]=""DR"",RC[-2],-RC[-2])"
        .Value = .Value
    End With
    ws.Range("I1").Value = "Signed amount"
    ws.Range("K1").Formula = "=SUM(I2:I" & lastRow & ")"
End Sub

Sub FlagExceptions()
    Dim ws As Worksheet, r As Long, n As Long
    Set ws = ThisWorkbook.Sheets("Data")
    n = 0
    For r = 2 To ws.Cells(ws.Rows.Count, "A").End(xlUp).Row
        If ws.Cells(r, 9).Value = 0 Or ws.Cells(r, 2).Value = "" Then
            ws.Cells(r, 10).Value = "CHECK"
            n = n + 1
        End If
    Next r
    ws.Range("A2:J20000").FormatConditions.Delete
    ws.Range("A2:J20000").FormatConditions.Add Type:=xlExpression, Formula1:="=$J2=""CHECK"""
    ws.Range("A2:J20000").FormatConditions(1).Interior.Color = 13421823
    ThisWorkbook.Sheets("Control").Range("C4").Value = n & " rows to check"
End Sub

Sub SaveDatedCopy()
    Dim target As String
    target = ThisWorkbook.Sheets("Control").Range("B5").Value & "\\Close_" & Format(Date, "yyyymmdd") & ".xlsx"
    ThisWorkbook.Sheets("Data").Copy
    ActiveWorkbook.SaveAs Filename:=target
    ActiveWorkbook.Close
    MsgBox "Saved " & target
End Sub
'''


def sales_workflow():
    wb = WB()
    wb.container(["Prepare orders"], 100)
    wb.container(["Summarise"], 101)
    p, q = ("Prepare orders",), ("Summarise",)
    wb.input(1, "Input\\orders.csv", {"OrderID": S, "OrderDate": S, "Region": S, "Product": S, "Qty": S, "UnitPrice": S,
                                       "Status": S}, box=p, CodePage=65001, Delimeter=",", HeaderRow="True")
    wb.cleanse(2, ["Region", "Product", "Status"], box=p)
    wb.filter(3, '[Status] != "Cancelled"', box=p)
    wb.select(4, [("Qty", True, None, I), ("UnitPrice", True, None, D), ("*Unknown", True)], box=p)
    wb.datetime(5, "OrderDate", "dd/MM/yyyy", "Order Day", box=p)
    wb.formula(6, [("Month", 'DateTimeFormat([Order Day], "%Y-%m")'), ("Gross", "[Qty] * [UnitPrice]", D),
                   ("Discount", "IF [Gross] >= 10000 THEN 0.1 ELSEIF [Gross] >= 5000 AND [Region] IN ('North','East') "
                                "THEN 0.05 ELSE 0 ENDIF", D),
                   ("Net", "Round([Gross] * (1 - [Discount]), 0.01)", D)], box=p)
    wb.unique(7, ["OrderID"], box=p)
    wb.output(8, "Output\\duplicate_orders.csv", box=p)
    wb.text_input(9, ["Product", "Category"], [["Widget", "Hardware"], ["Gadget", "Hardware"], ["Support", "Services"]],
                  box=q)
    wb.join(10, ["Product"], ["Product"], [("Right_Product", False), ("*Unknown", True)], box=q)
    wb.formula(11, [("Category", '"Unknown"')], box=q)
    wb.union(12, box=q)
    wb.summarize(13, [("Region", "GroupBy", "Region"), ("Month", "GroupBy", "Month"), ("Category", "GroupBy", "Category"),
                      ("Net", "Sum", "Net sales"), ("OrderID", "CountDistinct", "Orders")], box=q)
    wb.sort(14, [("Region", "Ascending"), ("Month", "Ascending")], box=q)
    wb.output(15, "Output\\sales_summary.xlsx|||Summary", box=q, OutputOption="Overwrite")
    wb.summarize(16, [("Region", "GroupBy", "Region"), ("OrderID", "Count", "Cancelled orders")], box=p)
    wb.output(17, "Output\\cancelled_by_region.csv", box=p)
    for a, b, sa, da in ((1, 2, "Output", "Input"), (2, 3, "Output", "Input"), (3, 4, "True", "Input"), (4, 5, "Output", "Input"),
                         (5, 6, "Output", "Input"), (6, 7, "Output", "Input"), (7, 8, "Duplicates", "Input"),
                         (7, 10, "Unique", "Left"), (9, 10, "Output", "Right"), (10, 11, "Left", "Input"),
                         (12, 13, "Output", "Input"), (13, 14, "Output", "Input"), (14, 15, "Output", "Input"),
                         (3, 16, "False", "Input"), (16, 17, "Output", "Input")):
        wb.conn(a, b, sa=sa, da=da)
    wb.conn(10, 12, sa="Join", name="#1")
    wb.conn(11, 12, name="#2")
    return wb


def dataset_audit():
    wb = WB()
    imp, audit, checks, old = ("Import and consolidate",), ("Dataset audit",), ("Dataset audit", "Checks"), ("Old checks",)
    for path, tid, off in ((imp, 200, False), (audit, 201, False), (checks, 202, False), (old, 203, True)):
        wb.container(list(path), tid, disabled=off)
    cols = [("Name", "V_String"), ("Owner", "V_String"), ("CreationMode", "V_String"), ("Scenario", "V_String"),
            ("Tags", "V_String"), ("DQScore", "Double"), ("Virtualizable", "V_String"), ("SizeGB", "Double"),
            ("Access", "V_String")]
    wb.notes = {41: "CreationMode", 42: "Scenario", 43: "Tags", 44: "Data Quality", 45: "Virtualizable", 46: "Read/Write",
                30: "Newest consolidated file", 90: "All issues"}
    wb.metas = {36: {"Output": cols}, 51: {"Output": cols + [("Issue", "V_WString")]},
                54: {"Output": cols + [("Issue", "V_WString")]}}
    # container 1: export -> consolidated workbook
    wb.input(1, "Input\\datasets_export.csv", dict(cols), box=imp, CodePage=65001, Delimeter=",", HeaderRow="True")
    wb.cleanse(2, ["Name", "Owner", "Tags"], box=imp)
    wb.formula(3, [("Load Date", "DateTimeToday()", "Date")], box=imp)
    wb.output(4, "Output\\Consolidated Datasets.xlsx|||Datasets", box=imp, OutputOption="Overwrite")
    for a, b in ((1, 2), (2, 3), (3, 4)):
        wb.conn(a, b)
    # container 2: find the newest consolidated file and read it
    wb.directory(30, "Output", "*.xlsx", box=audit)
    wb.filter(31, 'Contains([FileName], "Consolidated") AND !Contains([FileName], ".bak")', box=audit)
    wb.sort(32, [("CreationTime", "Descending")], box=audit)
    wb.sample(33, "First", 1, box=audit)
    wb.formula(34, [("Source", "[FullPath] + '|||Datasets'")], box=audit)
    wb.dynamic_input(36, "Output\\template.xlsx|||Datasets", "Source", dict(cols), box=audit)
    for a, b, sa in ((30, 31, "Output"), (31, 32, "True"), (32, 33, "Output"), (33, 34, "Output"), (34, 36, "Output")):
        wb.conn(a, b, sa=sa)
    # the six checks, each a short branch
    wb.filter(41, '[CreationMode] = "Manual"', box=checks)
    wb.formula(51, [("Issue", '"Created by hand"')], box=checks)
    wb.select(61, [("Name", True), ("Owner", True), ("Issue", True), ("*Unknown", False)], box=checks)
    wb.filter(42, simple=("Scenario", "IsEmpty", ""), box=checks)
    wb.formula(52, [("Issue", '"No scenario"')], box=checks)
    wb.summarize(62, [("Owner", "GroupBy", "Owner"), ("Issue", "GroupBy", "Issue"), ("Name", "Count", "Datasets"),
                      ("Name", "Concat", "Name", ", ")], box=checks)
    wb.filter(43, '!Contains([Tags], "certified")', box=checks)
    wb.formula(53, [("Issue", 'IF Contains([Tags], "draft") THEN "Draft tag" ELSE "Not certified" ENDIF')], box=checks)
    wb.text_input(70, ["Owner", "Team"], [["ana", "Finance"], ["bo", "Risk"]], box=checks)
    wb.join(63, ["Owner"], ["Owner"], [("Right_Owner", False), ("*Unknown", True)], box=checks)
    wb.filter(44, "[DQScore] < 0.8", box=checks)
    wb.formula(54, [("Issue", '"Low data quality: " + ToString([DQScore], 2)')], box=checks)
    wb.raw(64, "RegEx", "<Field>Name</Field><RegExExpression value=\"(\\w+)_v\\d+\" /><Method>ParseSimple</Method>", box=checks)
    wb.filter(45, '[Virtualizable] = "N" AND [SizeGB] > 100', box=checks)
    wb.formula(55, [("Issue", '"Large and not virtualizable"')], box=checks)
    wb.sort(65, [("SizeGB", "Descending")], box=checks)
    wb.filter(46, '[Access] IN ("RW", "W")', box=checks)
    wb.formula(56, [("Issue", 'IF [Access] = "W" THEN "Write only" ELSE "Read/write" ENDIF')], box=checks)
    wb.select(66, [("Access", False), ("*Unknown", True)], box=checks)
    for f in (41, 42, 43, 44, 45, 46):
        wb.conn(36, f)
        wb.conn(f, f + 10, sa="True")
        if f != 43:
            wb.conn(f + 10, f + 20)
    wb.conn(53, 63, da="Left"); wb.conn(70, 63, da="Right")
    wb.union(90, box=audit)
    for n, src in enumerate((61, 62, 63, 64, 65, 66), 1):
        wb.conn(src, 90, sa="Join" if src == 63 else "Output", name="#%d" % n)
    wb.formula(91, [("Audit Date", "DateTimeToday()", "Date")], box=audit)
    wb.output(92, "Output\\Dataset audit.xlsx|||Issues", box=audit, OutputOption="Overwrite")
    wb.conn(90, 91); wb.conn(91, 92)
    # a disabled container: not run, not described
    wb.input(100, "Input\\old_rules.csv", box=old)
    wb.formula(101, [("Old", "1")], box=old)
    wb.conn(100, 101)
    return wb


def main(write=True):
    files = {"dataset_audit.yxmd": dataset_audit().xml(), "sales_report.yxmd": sales_workflow().xml(), "month_end.bas": VBA}
    import tempfile
    tmp = tempfile.mkdtemp()
    if write:
        for name, text in files.items():
            with open(os.path.join(HERE, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    for stem in ("dataset_audit", "sales_report"):
        path = os.path.join(tmp, stem + ".yxmd")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(files[stem + ".yxmd"])
        doc = guide.build(workflow.Workflow(path))
        doc[1] = ("p", re.sub(r" \(sha256 [0-9a-f]+…\)", "", doc[1][1]))     # keep the examples free of a file hash
        files[stem + "_guide.md"] = logicdoc.to_markdown(doc)
        files[stem + "_guide.html"] = logicdoc.to_html(doc, "Migration guide: %s.yxmd" % stem)
    path = os.path.join(tmp, "dataset_audit.yxmd")
    files["dataset_audit_test.ipynb"] = notebook.build(workflow.Workflow(path), "dataset_audit_generated",
                                                       r"C:\path\to\workflow_folder")
    modules = [vbaparse.parse_module(VBA, "MonthEnd")]
    _, report = transpile.generate(modules, "month_end.bas")
    vdoc = analyse.build(modules, report, "month_end.bas")
    files["month_end_logic.md"] = logicdoc.to_markdown(vdoc)
    files["month_end_logic.html"] = logicdoc.to_html(vdoc, "VBA logic of month_end.bas")
    if write:
        for name in [n for n in files if "_logic." in n or "_guide." in n or "_test." in n]:
            with open(os.path.join(HERE, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(files[name])
    return files


if __name__ == "__main__":
    for name, text in main().items():
        print("examples/%s: %d lines" % (name, text.count("\n")))
