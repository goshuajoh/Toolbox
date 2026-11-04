"""Drive real Excel via COM (Windows + Office; pip install pywin32): recalc, refresh, convert, macros, PivotTables, VBA."""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def excel_app(visible=False):
    """Separate Excel instance that is ALWAYS quit (never touches the user's open Excel, no zombie EXCEL.EXE)."""
    import win32com.client as win32
    xl = win32.DispatchEx("Excel.Application")
    xl.Visible = visible
    xl.DisplayAlerts = False
    xl.ScreenUpdating = False
    try:
        yield xl
    finally:
        try:
            xl.Quit()
        except Exception:
            pass


def excel_open(xl, path, read_only=False):
    """Open a workbook in an excel_app() instance (absolute path required by COM)."""
    return xl.Workbooks.Open(str(Path(path).resolve()), ReadOnly=read_only)


def excel_recalculate(path):
    """Recalculate all formulas and save (openpyxl writes formulas but cannot compute them)."""
    with excel_app() as xl:
        wb = excel_open(xl, path)
        xl.CalculateFull()
        wb.Save()
        wb.Close()


def excel_refresh(path):
    """Refresh Power Query / connections / pivots, wait for them, save."""
    with excel_app() as xl:
        wb = excel_open(xl, path)
        wb.RefreshAll()
        xl.CalculateUntilAsyncQueriesDone()
        wb.Save()
        wb.Close()


def excel_convert(path, to=".xlsx"):
    """Convert with Excel itself: .xls/.xlsb/.csv -> .xlsx/.xlsm/.csv/.pdf (csv = active sheet only)."""
    fmts = {".xlsx": 51, ".xlsm": 52, ".xls": 56, ".csv": 6, ".xlsb": 50}
    src = Path(path).resolve()
    dst = src.with_suffix(to)
    with excel_app() as xl:
        wb = excel_open(xl, src, read_only=True)
        if to == ".pdf":
            wb.ExportAsFixedFormat(0, str(dst))
        else:
            wb.SaveAs(str(dst), FileFormat=fmts[to])
        wb.Close(False)
    return dst


def excel_run_macro(path, macro, *args, save=True):
    """Run a VBA Sub/Function in the workbook with optional arguments; returns the Function result."""
    with excel_app() as xl:
        wb = excel_open(xl, path)
        result = xl.Run(f"'{wb.Name}'!{macro}", *args)
        if save:
            wb.Save()
        wb.Close(False)
    return result


def excel_pivot(path, source_sheet, pivot_sheet, rows, values, columns=(), filters=(), func="sum",
                name="PivotTable1"):
    """Build a real Excel PivotTable (users can drill in) from a sheet's data; replaces pivot_sheet."""
    agg = {"sum": -4157, "count": -4112, "average": -4106, "max": -4136, "min": -4139}
    with excel_app() as xl:
        wb = excel_open(xl, path)
        src = wb.Worksheets(source_sheet).UsedRange
        try:
            wb.Worksheets(pivot_sheet).Delete()
        except Exception:
            pass
        dest = wb.Worksheets.Add()
        dest.Name = pivot_sheet
        pt = wb.PivotCaches().Create(SourceType=1, SourceData=src).CreatePivotTable(
            TableDestination=dest.Range("A3"), TableName=name)
        for f in rows:
            pt.PivotFields(f).Orientation = 1   # row
        for f in columns:
            pt.PivotFields(f).Orientation = 2   # column
        for f in filters:
            pt.PivotFields(f).Orientation = 3   # page filter
        for f in values:
            pt.AddDataField(pt.PivotFields(f), f"{func.title()} of {f}", agg[func])
        wb.Save()
        wb.Close()


def excel_sheet_to_pdf(path, sheet, pdf_path=None):
    """Export one sheet to PDF (report pack for email)."""
    pdf = Path(pdf_path or Path(path).with_suffix(".pdf")).resolve()
    with excel_app() as xl:
        wb = excel_open(xl, path, read_only=True)
        wb.Worksheets(sheet).ExportAsFixedFormat(0, str(pdf))
        wb.Close(False)
    return pdf


def vba_add_module(path, code, module_name="PyModule"):
    """Insert VBA source into a workbook and save as .xlsm.
    Needs Excel: File > Options > Trust Center > 'Trust access to the VBA project object model'."""
    out = Path(path).resolve().with_suffix(".xlsm")
    with excel_app() as xl:
        wb = excel_open(xl, path)
        mod = wb.VBProject.VBComponents.Add(1)   # 1 = standard module
        mod.Name = module_name
        # 'Attribute VB_Name' lines belong to .bas files only; AddFromString would treat them as code
        mod.CodeModule.AddFromString("\n".join(ln for ln in code.splitlines() if not ln.startswith("Attribute ")))
        wb.SaveAs(str(out), FileFormat=52)
        wb.Close()
    return out


def vba_import_bas(path, bas_files):
    """Import .bas module files (e.g. from the vba/ folder) into a workbook and save as .xlsm (Trust access needed)."""
    out = Path(path).resolve().with_suffix(".xlsm")
    with excel_app() as xl:
        wb = excel_open(xl, path)
        for f in ([bas_files] if isinstance(bas_files, (str, Path)) else bas_files):
            wb.VBProject.VBComponents.Import(str(Path(f).resolve()))
        wb.SaveAs(str(out), FileFormat=52)
        wb.Close()
    return out


def excel_save_as_csv_utf8(path, sheet, csv_path=None):
    """Save one sheet as UTF-8 CSV using Excel itself (keeps Excel's own number/date display text)."""
    dst = Path(csv_path or Path(path).with_suffix(".csv")).resolve()
    with excel_app() as xl:
        wb = excel_open(xl, path, read_only=True)
        wb.Worksheets(sheet).Activate()
        wb.SaveAs(str(dst), FileFormat=62)   # 62 = xlCSVUTF8
        wb.Close(False)
    return dst


def vba_extract(path):
    """{module: source} from .xlsm/.xls without opening Excel (pip install oletools) — for porting macros."""
    from oletools.olevba import VBA_Parser
    vp = VBA_Parser(str(path))
    try:
        return {name: code for _, _, name, code in vp.extract_macros()} if vp.detect_vba_macros() else {}
    finally:
        vp.close()
