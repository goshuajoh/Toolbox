"""Write Excel reports with openpyxl (no Excel needed): styling, templates, tables, formulas, protection, CSV."""
import csv
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill, Protection
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo


def write_report(path, sheets, number_formats=None, header_color="1F4E78"):
    """{sheet: df} -> workbook with bold coloured header, frozen top row, column widths, number formats
    ({'amount': '#,##0.00', 'date': 'dd/mm/yyyy'})."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, frame in sheets.items():
            frame.to_excel(w, sheet_name=name[:31], index=False)
            ws = w.sheets[name[:31]]
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor=header_color)
            ws.freeze_panes = "A2"
            for idx, col in enumerate(frame.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in frame[col].head(200) if pd.notna(v)])
                ws.column_dimensions[get_column_letter(idx)].width = min(60, width + 2)
                fmt = (number_formats or {}).get(str(col))
                if fmt:
                    for (c,) in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                        c.number_format = fmt
    return Path(path)


def safe_write_excel(path, sheets, overwrite=False, **report_kw):
    """write_report via temp file + rename (never a half-written file); refuses to overwrite unless told."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path.name} exists; pass overwrite=True")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".xlsx")
    os.close(fd)
    try:
        write_report(tmp, sheets, **report_kw)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def write_into_template(template, output, sheets, start_cell="A2"):
    """Fill an existing formatted workbook (logos, styles, formulas elsewhere kept). sheets={'Data': df}.
    Old values in the target block are cleared first."""
    wb = load_workbook(template, keep_vba=str(template).lower().endswith(".xlsm"))
    col, row0 = coordinate_from_string(start_cell)
    c0 = column_index_from_string(col)
    for name, frame in sheets.items():
        ws = wb[name]
        for row in ws.iter_rows(min_row=row0, max_row=max(ws.max_row, row0), min_col=c0,
                                max_col=c0 + frame.shape[1] - 1):
            for cell in row:
                cell.value = None
        for i, rec in enumerate(frame.itertuples(index=False), start=row0):
            for j, v in enumerate(rec, start=c0):
                ws.cell(i, j).value = None if pd.isna(v) else (v.to_pydatetime() if isinstance(v, pd.Timestamp) else v)
    wb.save(output)
    return Path(output)


def format_as_table(path, sheet, name="Table1", style="TableStyleMedium2"):
    """Make the used range an Excel Table (filters, banding). Headers must be unique text."""
    wb = load_workbook(path)
    ws = wb[sheet]
    t = Table(displayName=name, ref=f"A1:{get_column_letter(ws.max_column)}{ws.max_row}")
    t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True)
    ws.add_table(t)
    wb.save(path)


def add_conditional_format(path, sheet, cell_range, formula, fill="FFC7CE", font="9C0006"):
    """Highlight where an Excel formula is TRUE: range 'A2:H500', formula '$F2<0' colours whole rows."""
    wb = load_workbook(path)
    ws = wb[sheet]
    ws.conditional_formatting.add(cell_range, FormulaRule(formula=[formula], font=Font(color=font),
                                  fill=PatternFill("solid", start_color=fill, end_color=fill)))
    wb.save(path)


def add_dropdown(path, sheet, cell_range, options, allow_blank=True):
    """Drop-down list in cells (e.g. APPROVED/REJECTED in a checker column)."""
    wb = load_workbook(path)
    ws = wb[sheet]
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=allow_blank,
                        showErrorMessage=True)
    ws.add_data_validation(dv)
    dv.add(cell_range)
    wb.save(path)


def add_formula_column(path, sheet, header, formula, first_row=2):
    """Live Excel formula down a new column, e.g. '=D{r}-E{r}'. Values appear when Excel recalculates."""
    wb = load_workbook(path)
    ws = wb[sheet]
    col, last = ws.max_column + 1, ws.max_row
    ws.cell(1, col).value = header
    for r in range(first_row, last + 1):
        ws.cell(r, col).value = formula.format(r=r)
    wb.save(path)
    return get_column_letter(col)


def add_totals_row(path, sheet, columns, label="Total"):
    """Bold SUBTOTAL(109) row under the data for the named header columns (respects filters)."""
    wb = load_workbook(path)
    ws = wb[sheet]
    hdr = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
    last = ws.max_row
    r = last + 1
    ws.cell(r, 1).value = label
    ws.cell(r, 1).font = Font(bold=True)
    for name in columns:
        L = get_column_letter(hdr[name])
        ws.cell(r, hdr[name]).value = f"=SUBTOTAL(109,{L}2:{L}{last})"
        ws.cell(r, hdr[name]).font = Font(bold=True)
    wb.save(path)


def highlight_cells(path, sheet, cells, color="FFFF00"):
    """Fill specific cells, e.g. ['C5', 'F12'] from compare_frames."""
    wb = load_workbook(path)
    ws = wb[sheet]
    for a in cells:
        ws[a].fill = PatternFill("solid", start_color=color, end_color=color)
    wb.save(path)


def protect_sheet(path, sheet, password, editable_cols=()):
    """Lock a sheet except the given column letters (take password from a secret store, not code)."""
    wb = load_workbook(path)
    ws = wb[sheet]
    for L in editable_cols:
        for cell in ws[L][1:]:
            cell.protection = Protection(locked=False)
    ws.protection.sheet = True
    ws.protection.password = password
    wb.save(path)


def split_to_files(df, by, out_dir, fmt="xlsx", prefix=""):
    """One file per value of `by` (e.g. per counterparty); returns paths."""
    out = []
    for val, chunk in df.groupby(by, dropna=False):
        val = val[0] if isinstance(val, tuple) else val
        safe = re.sub(r"[^\w.-]+", "_", str(val))[:80]
        p = Path(out_dir) / f"{prefix}{safe}.{fmt}"
        p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "xlsx":
            chunk.to_excel(p, index=False)
        else:
            chunk.to_csv(p, index=False)
        out.append(p)
    return out


def to_csv_strict(df, path, sep=",", date_format="%Y-%m-%d", decimals=2, quote_all=False, bom=False):
    """CSV in the exact shape an upload system expects (dates, decimals, quoting, CRLF, optional BOM)."""
    df.to_csv(path, index=False, sep=sep, date_format=date_format, float_format=f"%.{decimals}f",
              quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
              encoding="utf-8-sig" if bom else "utf-8", lineterminator="\r\n")
    return Path(path)


def write_cell(path, sheet, cell, value):
    """Set one cell ('B3') in an existing workbook (keeps everything else)."""
    wb = load_workbook(path, keep_vba=str(path).lower().endswith(".xlsm"))
    wb[sheet][cell].value = value
    wb.save(path)


def autofit_columns(path, sheet=None, max_width=60):
    """Set column widths from content for one or all sheets."""
    wb = load_workbook(path)
    for ws in ([wb[sheet]] if sheet else wb.worksheets):
        widths = {}
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    widths[c.column_letter] = max(widths.get(c.column_letter, 0), len(str(c.value)))
        for col, w in widths.items():
            ws.column_dimensions[col].width = min(max_width, w + 2)
    wb.save(path)


def unhide_all_sheets(path):
    """Make every hidden / very-hidden sheet visible (legacy workbooks hide lookup tabs)."""
    wb = load_workbook(path, keep_vba=str(path).lower().endswith(".xlsm"))
    names = [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]
    for ws in wb.worksheets:
        ws.sheet_state = "visible"
    wb.save(path)
    return names


def sheets_to_csv(path, out_dir, encoding="utf-8-sig"):
    """Every sheet -> its own CSV (values only); returns paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in pd.read_excel(path, sheet_name=None, engine="openpyxl").items():
        safe = re.sub(r"[^\w.-]+", "_", name)
        p = out_dir / f"{safe}.csv"
        df.to_csv(p, index=False, encoding=encoding)
        paths.append(p)
    return paths


def col_letter(n):
    """1 -> 'A', 28 -> 'AB'."""
    return get_column_letter(n)


def col_index(letter):
    """'A' -> 1, 'AB' -> 28."""
    return column_index_from_string(letter)
