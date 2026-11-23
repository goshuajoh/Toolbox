Attribute VB_Name = "MonthEnd"
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
    target = ThisWorkbook.Sheets("Control").Range("B5").Value & "\Close_" & Format(Date, "yyyymmdd") & ".xlsx"
    ThisWorkbook.Sheets("Data").Copy
    ActiveWorkbook.SaveAs Filename:=target
    ActiveWorkbook.Close
    MsgBox "Saved " & target
End Sub
