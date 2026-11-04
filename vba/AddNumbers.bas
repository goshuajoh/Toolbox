Attribute VB_Name = "AddNumbers"
' Example Function callable from Python: excel_run_macro(path, "AddNumbers", 2, 3) -> 5
Function AddNumbers(a As Double, b As Double) As Double
    AddNumbers = a + b
End Function
