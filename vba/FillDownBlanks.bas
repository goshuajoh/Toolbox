Attribute VB_Name = "FillDownBlanks"
' Fill blanks in the selection from the cell above, then freeze as values.
Sub FillDownBlanks()
    On Error Resume Next
    Selection.SpecialCells(xlCellTypeBlanks).FormulaR1C1 = "=R[-1]C"
    On Error GoTo 0
    Selection.Value = Selection.Value
End Sub
