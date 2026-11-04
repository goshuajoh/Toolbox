Attribute VB_Name = "DeleteEmptyRows"
' Delete completely empty rows in the used range of the active sheet.
Sub DeleteEmptyRows()
    Dim r As Long, ws As Worksheet: Set ws = ActiveSheet
    For r = ws.UsedRange.Row + ws.UsedRange.Rows.Count - 1 To ws.UsedRange.Row Step -1
        If Application.WorksheetFunction.CountA(ws.Rows(r)) = 0 Then ws.Rows(r).Delete
    Next r
End Sub
