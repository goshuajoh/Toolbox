"""Read Excel / CSV / fixed-width / folders the way bank exports actually arrive."""
import csv
import glob
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .clean import clean_headers, require_columns


def read_sheet(path, sheet=0, header_row=1, required=(), **kw):
    """Excel sheet with header on row N (1-based); blank rows dropped, headers tidied (clean_headers).
    required = columns that must exist, matched tolerant of case / spaces / punctuation and renamed to the
    spelling you asked for; missing -> MissingColumns naming the near misses."""
    df = pd.read_excel(path, sheet_name=sheet, header=header_row - 1, engine="openpyxl", **kw).dropna(how="all")
    return require_columns(clean_headers(df), required, source=Path(path).name)


def read_csv_smart(path, required=(), **kw):
    """CSV with unknown encoding (BOM/cp1252/latin-1) and delimiter (, ; tab |); headers tidied and `required`
    checked as in read_sheet. An empty file -> ValueError (a BusinessError once classified), not a crash."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                sample = fh.read(8192)
            try:
                delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delim = ","
            df = pd.read_csv(path, sep=delim, encoding=enc, **kw)
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            raise ValueError(f"{Path(path).name} is empty (no header row)") from None
    else:
        raise ValueError(f"Could not decode {path}")
    return require_columns(clean_headers(df), required, source=Path(path).name)


def read_any(path, sheet=0, header_row=1, required=(), **kw):
    """Read xlsx/xlsm/xls/xlsb/csv/txt/parquet by extension (xls needs xlrd, xlsb needs pyxlsb); headers tidied
    and `required` checked as in read_sheet."""
    p = Path(path); ext = p.suffix.lower(); h = header_row - 1
    if ext in (".xlsx", ".xlsm"):
        return read_sheet(p, sheet, header_row, required, **kw)
    if ext in (".csv", ".txt"):
        return read_csv_smart(p, required, header=h, **kw)
    if ext == ".xls":
        df = pd.read_excel(p, sheet_name=sheet, header=h, engine="xlrd", **kw)
    elif ext == ".xlsb":
        df = pd.read_excel(p, sheet_name=sheet, header=h, engine="pyxlsb", **kw)
    elif ext == ".parquet":
        df = pd.read_parquet(p, **kw)
    else:
        raise ValueError(f"unsupported file type {ext}")
    return require_columns(clean_headers(df), required, source=p.name)


def list_sheets(path):
    """Sheet names of a workbook."""
    wb = load_workbook(path, read_only=True)
    try:
        return wb.sheetnames
    finally:
        wb.close()


def read_all_sheets(path, header_row=1, sheet_col="sheet"):
    """Every sheet stacked into one table, with a column naming the source sheet."""
    sheets = pd.read_excel(path, sheet_name=None, header=header_row - 1, engine="openpyxl")
    return pd.concat([d.assign(**{sheet_col: n}) for n, d in sheets.items() if not d.empty], ignore_index=True)


def read_folder(pattern, reader=None, source_col="source_file"):
    """All files matching a glob ('inbox/*.xlsx') stacked by column name, with the source file name."""
    files = sorted(glob.glob(str(pattern)))
    if not files:
        raise FileNotFoundError(f"no files match {pattern}")
    reader = reader or read_any
    return pd.concat([reader(f).assign(**{source_col: Path(f).name}) for f in files], ignore_index=True, sort=False)


def find_header_row(path, sheet=0, must_contain=(), scan_rows=30):
    """1-based row number of the first row containing all the given labels."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=scan_rows, engine="openpyxl")
    want = {str(x).strip().lower() for x in must_contain}
    for i, row in enumerate(raw.itertuples(index=False), start=1):
        if want <= {str(v).strip().lower() for v in row if pd.notna(v)}:
            return i
    raise ValueError(f"no header row with {sorted(want)} in first {scan_rows} rows")


def read_multirow_header(path, sheet=0, header_rows=(1, 2), sep=" | "):
    """Flatten stacked headers ('Q1' above 'Sales') into 'Q1 | Sales'; blank/merged top cells carried right."""
    df = pd.read_excel(path, sheet_name=sheet, header=[r - 1 for r in header_rows], engine="openpyxl")
    last, cols = [""] * len(header_rows), []
    for col in df.columns:
        parts = []
        for lvl, v in enumerate(col):
            v = "" if pd.isna(v) or str(v).startswith("Unnamed") else str(v).strip()
            if not v and lvl < len(col) - 1:
                v = last[lvl]
            last[lvl] = v
            parts.append(v)
        cols.append(sep.join(p for p in parts if p))
    df.columns = cols
    return df


def unmerge_fill(path, sheet=0, header_row=1):
    """Read a sheet with merged cells; every cell in a merged block gets the block's value."""
    wb = load_workbook(path)
    try:
        ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet]
        for rng in list(ws.merged_cells.ranges):
            val = ws.cell(rng.min_row, rng.min_col).value
            ws.unmerge_cells(str(rng))
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    ws.cell(r, c).value = val
        rows = list(ws.values)
    finally:
        wb.close()
    hdr = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[header_row - 1])]
    return pd.DataFrame(rows[header_row:], columns=hdr).dropna(how="all")


def read_excel_table(path, table_name):
    """Read a named Excel Table (Insert > Table) wherever it sits in the workbook."""
    wb = load_workbook(path, data_only=True)
    try:
        for ws in wb.worksheets:
            if table_name in ws.tables:
                rows = [[c.value for c in row] for row in ws[ws.tables[table_name].ref]]
                return pd.DataFrame(rows[1:], columns=rows[0])
        raise KeyError(f"table {table_name} not found")
    finally:
        wb.close()


def read_named_range(path, name):
    """Read a workbook defined name (e.g. 'FX_Rates'); first row = header."""
    wb = load_workbook(path, data_only=True)
    try:
        rows = []
        for sheet, ref in wb.defined_names[name].destinations:
            rows += [[c.value for c in row] for row in wb[sheet][ref.replace("$", "")]]
        return pd.DataFrame(rows[1:], columns=rows[0])
    finally:
        wb.close()


def read_fixed_width(path, spec, encoding="cp1252"):
    """Fixed-width mainframe file; spec = [("account", 0, 10), ("amount", 10, 25)] (end exclusive). All text."""
    return pd.read_fwf(path, colspecs=[(s, e) for _, s, e in spec], names=[n for n, _, _ in spec],
                       dtype=str, encoding=encoding)


def csv_in_chunks(path, func, chunksize=200_000, **read_kw):
    """Apply func(chunk) -> DataFrame to a CSV too big for memory; results stacked."""
    return pd.concat((func(ch) for ch in pd.read_csv(path, chunksize=chunksize, **read_kw)), ignore_index=True)


def read_parquet(path, columns=None):
    """Parquet file (Fabric/OneLake exports). Needs pyarrow."""
    return pd.read_parquet(path, columns=columns)


def write_parquet(df, path):
    """DataFrame -> Parquet (smaller and typed; what Fabric ingests). Needs pyarrow."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return Path(path)


def workbook_info(path):
    """Sheets with size, visibility, tables, merged-cell count; plus defined names and whether it has macros."""
    wb = load_workbook(path, keep_vba=str(path).lower().endswith(".xlsm"))
    try:
        sheets = [{"sheet": ws.title, "state": ws.sheet_state, "dims": ws.dimensions, "rows": ws.max_row,
                   "cols": ws.max_column, "tables": ",".join(ws.tables.keys()),
                   "merged_ranges": len(ws.merged_cells.ranges)} for ws in wb.worksheets]
        return {"sheets": pd.DataFrame(sheets), "names": list(wb.defined_names.keys()),
                "has_macros": bool(getattr(wb, "vba_archive", None))}
    finally:
        wb.close()


def find_in_workbook(path, text, exact=False, case=False):
    """Every cell containing text (or equal to it) across all sheets -> DataFrame sheet, cell, value."""
    wb = load_workbook(path, data_only=True, read_only=True)
    want = text if case else str(text).lower()
    hits = []
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.value is None:
                        continue
                    v = str(c.value) if case else str(c.value).lower()
                    if (v == want) if exact else (want in v):
                        hits.append({"sheet": ws.title, "cell": c.coordinate, "value": c.value})
    finally:
        wb.close()
    return pd.DataFrame(hits, columns=["sheet", "cell", "value"])


def read_formulas(path, sheet=0):
    """All formulas in a sheet -> DataFrame cell, formula (audit what a legacy workbook calculates)."""
    wb = load_workbook(path)
    try:
        ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet]
        rows = [{"cell": c.coordinate, "formula": c.value} for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=")]
    finally:
        wb.close()
    return pd.DataFrame(rows, columns=["cell", "formula"])


def read_cell(path, sheet, cell, formulas=False):
    """One cell's value ('B3'); cached result of a formula unless formulas=True."""
    wb = load_workbook(path, data_only=not formulas)
    try:
        ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet]
        return ws[cell].value
    finally:
        wb.close()


def read_range(path, sheet, cell_range, header=True):
    """A rectangular range ('B4:F20') -> DataFrame (first row = header if header)."""
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet]
        rows = [[c.value for c in row] for row in ws[cell_range]]
    finally:
        wb.close()
    return pd.DataFrame(rows[1:], columns=rows[0]) if header else pd.DataFrame(rows)
