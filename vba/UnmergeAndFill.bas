Attribute VB_Name = "UnmergeAndFill"
' Unmerge the selection; every cell gets the merged value.
Sub UnmergeAndFill()
    Dim c As Range, area As Range, v
    For Each c In Selection.Cells
        If c.MergeCells Then
            Set area = c.MergeArea: v = area.Cells(1, 1).Value
            area.UnMerge: area.Value = v
        End If
    Next c
End Sub
