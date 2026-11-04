Attribute VB_Name = "CombineWorkbooksInFolder"
' Append the first sheet of every .xlsx in a folder below the active sheet's data (header taken once).
Sub CombineWorkbooksInFolder()
    Dim folder As String, f As String, wb As Workbook, dest As Worksheet, src As Range, nextRow As Long, first As Boolean
    folder = InputBox("Folder path (ending with \)")
    If folder = "" Then Exit Sub
    Set dest = ActiveSheet: first = (Application.WorksheetFunction.CountA(dest.Cells) = 0)
    Application.ScreenUpdating = False
    f = Dir(folder & "*.xlsx")
    Do While f <> ""
        Set wb = Workbooks.Open(folder & f, ReadOnly:=True)
        Set src = wb.Worksheets(1).UsedRange
        nextRow = IIf(first, 1, dest.Cells(dest.Rows.Count, 1).End(xlUp).Row + 1)
        If first Then
            src.Copy dest.Cells(1, 1): first = False
        ElseIf src.Rows.Count > 1 Then
            src.Offset(1).Resize(src.Rows.Count - 1).Copy dest.Cells(nextRow, 1)
        End If
        wb.Close False
        f = Dir()
    Loop
    Application.ScreenUpdating = True
End Sub
