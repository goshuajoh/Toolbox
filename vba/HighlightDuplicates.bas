Attribute VB_Name = "HighlightDuplicates"
' Highlight duplicate values in the selected column(s) with conditional formatting.
Sub HighlightDuplicates()
    Dim fc As Object
    Set fc = Selection.FormatConditions.AddUniqueValues
    fc.DupeUnique = xlDuplicate
    fc.Interior.Color = RGB(255, 199, 206)
End Sub
