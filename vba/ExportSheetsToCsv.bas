Attribute VB_Name = "ExportSheetsToCsv"
' Export every sheet to its own UTF-8 CSV next to the workbook.
Sub ExportSheetsToCsv()
    Dim ws As Worksheet, p As String: p = ThisWorkbook.Path & "\"
    Application.DisplayAlerts = False
    For Each ws In ThisWorkbook.Worksheets
        ws.Copy
        ActiveWorkbook.SaveAs p & ws.Name & ".csv", xlCSVUTF8
        ActiveWorkbook.Close False
    Next ws
    Application.DisplayAlerts = True
End Sub
