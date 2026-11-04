Attribute VB_Name = "RefreshAllAndWait"
' Refresh all queries/pivots and wait until finished.
Sub RefreshAllAndWait()
    ThisWorkbook.RefreshAll
    Application.CalculateUntilAsyncQueriesDone
End Sub
