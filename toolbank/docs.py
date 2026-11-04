"""Word and PDF: fill templates, read text and tables (python-docx, pdfplumber)."""
import re
from pathlib import Path

import pandas as pd

_PH = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _all_paragraphs(d):
    paras = list(d.paragraphs)
    paras += [p for t in d.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    for s in d.sections:
        paras += list(s.header.paragraphs) + list(s.footer.paragraphs)
    return paras


def fill_docx(template, output, values, strict=True):
    """Replace {{placeholders}} in a Word file (body, tables, headers, footers). strict: error if any left."""
    import docx
    d = docx.Document(str(template))
    for p in _all_paragraphs(d):
        full = "".join(r.text for r in p.runs)
        if "{{" not in full:
            continue
        new = _PH.sub(lambda m: str(values.get(m.group(1), m.group(0))), full)
        if p.runs:
            p.runs[0].text = new
            for r in p.runs[1:]:
                r.text = ""
    left = sorted({m.group(1) for p in _all_paragraphs(d) for m in _PH.finditer(p.text)})
    if strict and left:
        raise ValueError(f"unfilled placeholders: {left}")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    d.save(str(output))
    return Path(output)


def docx_text(path):
    """All paragraph + table text of a Word file."""
    import docx
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    parts += ["\t".join(c.text for c in row.cells) for t in d.tables for row in t.rows]
    return "\n".join(parts)


def docx_tables(path, header=True):
    """Every table in a Word file as a DataFrame (first row = header)."""
    import docx
    out = []
    for t in docx.Document(str(path)).tables:
        rows = [[c.text.strip() for c in row.cells] for row in t.rows]
        if rows:
            out.append(pd.DataFrame(rows[1:], columns=rows[0]) if header else pd.DataFrame(rows))
    return out


def df_to_docx_table(path, df, heading=None, style="Table Grid"):
    """Append a DataFrame as a table to a Word file (created if missing)."""
    import docx
    p = Path(path)
    d = docx.Document(str(p)) if p.exists() else docx.Document()
    if heading:
        d.add_heading(heading, level=2)
    t = d.add_table(rows=1, cols=len(df.columns))
    t.style = style
    for i, c in enumerate(df.columns):
        t.rows[0].cells[i].text = str(c)
    for rec in df.itertuples(index=False):
        cells = t.add_row().cells
        for i, v in enumerate(rec):
            cells[i].text = "" if pd.isna(v) else str(v)
    d.save(str(p))
    return p


def pdf_text(path, pages=None):
    """Text of a (non-scanned) PDF; pages = list of 0-based page numbers or None for all. pip install pdfplumber."""
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        sel = pdf.pages if pages is None else [pdf.pages[i] for i in pages]
        return "\n".join(p.extract_text() or "" for p in sel)


def pdf_tables(path, pages=None):
    """Tables found in a PDF as DataFrames (first row = header). pip install pdfplumber."""
    import pdfplumber
    out = []
    with pdfplumber.open(str(path)) as pdf:
        sel = pdf.pages if pages is None else [pdf.pages[i] for i in pages]
        for p in sel:
            for t in p.extract_tables():
                if t and len(t) > 1:
                    out.append(pd.DataFrame(t[1:], columns=t[0]))
    return out


def pdf_find(path, pattern, flags=re.I):
    """All regex matches in a PDF's text (e.g. trade refs, amounts) as a list of dicts of named groups or strings."""
    text = pdf_text(path)
    rx = re.compile(pattern, flags)
    return [m.groupdict() if rx.groupindex else m.group(0) for m in rx.finditer(text)]
