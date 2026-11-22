Attribute VB_Name = "Module1"
' Synthetic macros written in the style of the Configuration.xlsm macros (recorded-macro idioms:
' Select/Selection chains, status cells, On Error handlers). No real data.

Sub FormatAll()
    Sheets("Format").Select
    Range("F2:I2").Select
    Selection.ClearContents

Call FormatTradesA
Call FormatTradesB

End Sub

Sub FormatTradesA()
Dim wbToFormat As Workbook
Dim wsToFormat As Worksheet
Dim intActiveRow As Integer
Dim wbConfig As Workbook

On Error GoTo ErrorHandler

Set wbConfig = ActiveWorkbook
Sheets("Format").Activate
format_file = [B2]
format_sheet = [A2]

Application.DisplayAlerts = False

    Workbooks.Open Filename:=format_file

    Set wbToFormat = ActiveWorkbook
    Sheets(format_sheet).Activate

        Range("A2:AS5000").Select
        Selection.Columns.AutoFit

        'Format Yellow for NO ACTION REQUIRED
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=COUNTIF($A2,""*NO ACTION REQUIRED*"")"
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 65535
                .TintAndShade = 0
            End With

        'Format Red for MANUAL
            Selection.FormatConditions(1).StopIfTrue = False
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=$B2=""MANUAL"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 8420607
                .TintAndShade = 0
            End With
            Selection.FormatConditions(1).StopIfTrue = False

        'Format Grey for HOLD
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:="=$A2=""HOLD"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .ThemeColor = xlThemeColorDark1
                .TintAndShade = -0.14996795556505
            End With
            Selection.FormatConditions(1).StopIfTrue = False

        'Format Red for MANUAL HOLD Override
        Range("B2:AS5000").Select
            Selection.FormatConditions(1).StopIfTrue = False
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=B2=""MANUAL"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 8420607
                .TintAndShade = 0
            End With
            Selection.FormatConditions(1).StopIfTrue = False
        Range("A1").Select

    wbToFormat.Save
    wbToFormat.Close

    If ActiveSheet.AutoFilterMode Then ActiveSheet.AutoFilterMode = False

wbConfig.Activate
Sheets("Format").Activate
[F2] = "Successfully Formatted"

[A1].Select

Exit Sub

ErrorHandler:
wbConfig.Activate
  Sheets("Format").Activate
  [F2] = "Fail - " & Err.Description

End Sub

Sub FormatTradesB()
Dim wbToFormat As Workbook
Dim wsToFormat As Worksheet
Dim intActiveRow As Integer
Dim wbConfig As Workbook

On Error GoTo ErrorHandler

Set wbConfig = ActiveWorkbook
Sheets("Format").Activate
format_file = [D2]
format_sheet = [A2]

Application.DisplayAlerts = False

    Workbooks.Open Filename:=format_file

    Set wbToFormat = ActiveWorkbook
    Sheets(format_sheet).Activate

        Range("A2:AS5000").Select
        Selection.Columns.AutoFit

        'Format Yellow for NO ACTION REQUIRED
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=COUNTIF($A2,""*NO ACTION REQUIRED*"")"
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 65535
                .TintAndShade = 0
            End With

        'Format Red for MANUAL
            Selection.FormatConditions(1).StopIfTrue = False
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=$B2=""MANUAL"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 8420607
                .TintAndShade = 0
            End With
            Selection.FormatConditions(1).StopIfTrue = False

        'Format Grey for HOLD
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:="=$A2=""HOLD"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .ThemeColor = xlThemeColorDark1
                .TintAndShade = -0.14996795556505
            End With
            Selection.FormatConditions(1).StopIfTrue = False

        'Format Red for MANUAL HOLD Override
        Range("B2:AS5000").Select
            Selection.FormatConditions(1).StopIfTrue = False
            Selection.FormatConditions.Add Type:=xlExpression, Formula1:= _
                "=B2=""MANUAL"""
            Selection.FormatConditions(Selection.FormatConditions.Count).SetFirstPriority
            With Selection.FormatConditions(1).Interior
                .PatternColorIndex = xlAutomatic
                .Color = 8420607
                .TintAndShade = 0
            End With
            Selection.FormatConditions(1).StopIfTrue = False
        Range("A1").Select

    wbToFormat.Save
    wbToFormat.Close

    If ActiveSheet.AutoFilterMode Then ActiveSheet.AutoFilterMode = False

wbConfig.Activate
Sheets("Format").Activate
[H2] = "Successfully Formatted"

[A1].Select

Exit Sub

ErrorHandler:
wbConfig.Activate
  Sheets("Format").Activate
  [H2] = "Fail - " & Err.Description

End Sub
Attribute VB_Name = "Module3"
Option Explicit

Sub ImportAll()
    Call PurgeAllImport
    Call ImportTradesA
    Call ImportLifecycle
    UpdateLookupColumns "TradesA", "LOOKUP_A"
End Sub

Sub PurgeAllImport()
    Sheets("Import").Select
    Range("O2:U2").Select
    Selection.ClearContents
    Sheets("TradesA").Select
    Rows("3:20000").Select
    Selection.Delete Shift:=xlUp
    Sheets("Lifecycle").Rows("3:20000").Delete
    Sheets("Import").Select
End Sub

Sub ImportTradesA()
Dim wbCopyFrom As Workbook
Dim wbConfig As Workbook
Dim intActiveRow As Long
Dim source_file As String, source_sheet As String

On Error GoTo ErrorHandler

Set wbConfig = ActiveWorkbook
Sheets("Import").Activate
source_file = [A2]
source_sheet = [B2]

Application.DisplayAlerts = False
Workbooks.Open Filename:=source_file
Set wbCopyFrom = ActiveWorkbook
Sheets(source_sheet).Activate

intActiveRow = Range("A50000").End(xlUp).Row

If Range("A2").Value = "" Then
Else
    Range("A2:AQ" & intActiveRow).Copy
    wbConfig.Activate
    Sheets("TradesA").Activate
    Range("C3").Select
    ActiveSheet.Paste
End If

wbCopyFrom.Close
wbConfig.Activate
Sheets("Import").Activate
[O2] = "Successfully Imported"
Exit Sub

ErrorHandler:
wbConfig.Activate
Sheets("Import").Activate
[O2] = "Fail - " & Err.Description
End Sub

Sub ImportLifecycle()
Dim wbCopyFrom As Workbook, wbConfig As Workbook
Dim intActiveRow As Long, intLastRow As Long
Dim source_file As String, source_sheet As String

On Error GoTo ErrorHandler
Set wbConfig = ActiveWorkbook
Sheets("Import").Activate
source_file = [I2]
source_sheet = [J2]
Workbooks.Open Filename:=source_file
Set wbCopyFrom = ActiveWorkbook
Sheets(source_sheet).Activate
intActiveRow = Range("A50000").End(xlUp).Row
If Range("A2").Value = "" Then
Else
    Range("A2:P" & intActiveRow).Copy
    wbConfig.Sheets("Lifecycle").Range("C3").PasteSpecial Paste:=xlPasteValues
    Range("Q2:V" & intActiveRow).Copy
    wbConfig.Sheets("Lifecycle").Range("W3").PasteSpecial Paste:=xlPasteValues
    Range("W2:AK" & intActiveRow).Copy
    wbConfig.Sheets("Lifecycle").Range("AE3").PasteSpecial Paste:=xlPasteValues

    'Update cells
    wbConfig.Activate
    Sheets("Lifecycle").Activate
    intLastRow = Range("C50000").End(xlUp).Row
    Range("AT3").FormulaR1C1 = "=RC[-10]&"" ""&RC[-4]"
    Range("AU3").FormulaR1C1 = "=RC[-9]&"" ""&RC[-10]"
    Range("AT3:AT10000").Copy
    Range("AJ3").PasteSpecial Paste:=xlPasteValues
    Range("AU3:AU" & intLastRow).Copy
    Range("AL3").PasteSpecial Paste:=xlPasteValues
    Columns("AT:AX").ClearContents
End If
wbCopyFrom.Close
wbConfig.Activate
Sheets("Import").Activate
[S2] = "Successfully Imported"
Exit Sub
ErrorHandler:
wbConfig.Activate
Sheets("Import").Activate
[S2] = "Fail - " & Err.Description
End Sub

Sub UpdateLookupColumns(dataSheet As String, lookupSheet As String)
    Dim ws As Worksheet
    Dim lastRow As Long
    Set ws = ThisWorkbook.Sheets(dataSheet)
    lastRow = ws.Cells(ws.Rows.Count, "g").End(xlUp).Row
    If lastRow < 3 Then Exit Sub
    With ws.Range("I3:I" & lastRow)
        .Formula = "=RIGHT(XLOOKUP(G3,'" & lookupSheet & "'!E:E,'" & lookupSheet & "'!C:C,""""),7)"
        .Value = .Value
    End With
End Sub

Function CountFilled(sheetName As String) As Long
    Dim n As Long, i As Long
    n = 0
    For i = 3 To 50
        Select Case Sheets(sheetName).Cells(i, 3).Value
            Case "", "n/a"
            Case Else
                n = n + 1
        End Select
    Next i
    CountFilled = n
End Function
Attribute VB_Name = "Module5"
Sub UpdateStats()
Dim wbStats As Workbook
Dim rngDate As Range
On Error GoTo ErrorHandler
Sheets("Configuration").Activate
Stats_file = [T2]
strDate = [E2]
Workbooks.Open Filename:=Stats_file
Set wbStats = ActiveWorkbook
Set rngDate = Cells.Find(What:=DateValue(strDate), LookAt:=xlPart, SearchOrder:=xlByRows)
intCol = rngDate.Column
wbStats.Save
wbStats.Close
Exit Sub
ErrorHandler:
Sheets("Stats").Activate
[G2] = "Fail - " & Err.Description
End Sub

Sub MailScan()
    Dim olApp As Object
    Dim olFolder As Object
    Set olApp = CreateObject("Outlook.Application")
    Set olFolder = olApp.GetNamespace("MAPI").GetDefaultFolder(6)
    For Each olMail In olFolder.Items
        If InStr(olMail.Subject, "Match Key") > 0 Then
            Sheets("MailScan").Cells(16, 1).Value = olMail.Subject
        End If
    Next
    MsgBox "Done"
End Sub
