Attribute VB_Name = "TrimAllCells"
' Trim spaces and non-breaking spaces from every text cell in the selection.
Sub TrimAllCells()
    Dim c As Range
    For Each c In Selection.Cells
        If Not c.HasFormula And VarType(c.Value) = vbString Then
            c.Value = Application.WorksheetFunction.Trim(Replace(c.Value, Chr(160), " "))
        End If
    Next c
End Sub
