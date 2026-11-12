"""
title: Reconcile two sources and report the breaks
order: 70
when: >-
  Two extracts of the same items (ledger and bank statement, front and back office) must agree on amount, currency
  and value date per reference, and a person works the differences from an Excel report.
tags: [reconciliation, matching, tolerance, excel, report, normalise]
needs: []
blocks: [BusinessError, col_letter, highlight_cells, normalise_currency, normalise_key, recon_summary, reconcile, to_date, to_number, write_report]
decisions:
  - "Normalise both sides the same way before matching: normalise_key padded to 10 characters (' ab-77' = 'AB77 ', '45' = '0045'; a longer ref is kept whole), to_number ('(75.00)' = -75), to_date (day-first text, ISO, Excel serials), normalise_currency ('S$' = 'SGD')."
  - "A ref column read as numbers is made whole (Int64) first: normalise_key turns 123.0 into '1230'. Read refs of 16+ digits as text (dtype=str): Excel keeps only 15 digits of a number."
  - "Amounts match within TOL_CENTS = 1 cent, set per recon: a 4-decimal source against a 2-decimal one (0.004 apart) matches, 0.02 apart is a break. 1 cent also hides a real 1-cent break, so the Summary counts the matches that used the tolerance. Use 0 for cash."
  - "Currency and value date must be equal: a date one day off is a break, not a tolerance."
  - "Duplicate refs on one side (after normalising) raise BusinessError DUPLICATE_KEYS and nothing is matched: which of the two rows pairs with the other side would be a guess."
  - "A blank or unreadable amount, currency or date raises BusinessError UNREADABLE with the source rows: reconcile counts blank against blank as equal, so two broken rows would match."
  - "A blank ref does not stop the run: the row gets the key NOREF-L-<row> or NOREF-R-<row> and lands in Left only or Right only."
  - "Breaks are a result, not an error: BREAK, LEFT_ONLY and RIGHT_ONLY rows go to the report, and the robot returns exit code 2 when there are any (the template's code for 'send the report to the owner')."
governance:
  - "Every row keeps its source row number (row_l, row_r = index + 2: the Excel row when the header is on row 1), so a reviewer can find it in the input file."
  - "Summary lists all four statuses (zeros included) and each side's row count: matched + break + left only = rows left, and the same for the right."
  - "Summary counts rows. It never adds amounts across currencies."
  - "Differing cells are yellow on the Breaks sheet. Dates show as dd-mmm-yyyy (06-Mar-2026), which nobody reads as 3 June."
  - "The report is built under a temporary name and renamed when complete, so nobody opens a half-written file. Nothing is written when the run stops."
  - "The log line carries counts per status only; refs and amounts stay in the report."
  - "The tolerance is a control setting: the Summary shows it, and a change goes through change control."
"""
import os
from pathlib import Path

import pandas as pd

from toolbank import (BusinessError, col_letter, highlight_cells, normalise_currency, normalise_key, recon_summary,
                      reconcile, to_date, to_number, write_report)

TOL_CENTS = 1                                             # amounts this many cents apart still match; 0 for cash
WIDTH = 10                                                # refs are zero-padded to this many characters
COMPARE = ["amount", "ccy", "value_date"]
STATUSES = ["MATCHED", "BREAK", "LEFT_ONLY", "RIGHT_ONLY"]
BREAK_VIEW = ["key", "break_cols", "amount_l", "amount_r", "amount_diff", "ccy_l", "ccy_r", "value_date_l",
              "value_date_r", "row_l", "row_r"]
FORMATS = {"amount_l": "#,##0.00", "amount_r": "#,##0.00", "amount_diff": "#,##0.00",
           "value_date_l": "dd-mmm-yyyy", "value_date_r": "dd-mmm-yyyy"}


def normalise(df, side, ref, amount, ccy, value_date):
    """One side -> key, amount, ccy, value_date, row; stops on unreadable values or duplicate refs."""
    refs = df[ref].astype("Int64") if pd.api.types.is_numeric_dtype(df[ref]) else df[ref]   # 123.0 -> 123
    key, row = normalise_key(refs), pd.Series(df.index + 2, index=df.index)
    out = pd.DataFrame({"key": key.str.zfill(WIDTH).where(key.fillna("") != "", f"NOREF-{side}-" + row.astype(str)),
                        "amount": to_number(df[amount]), "ccy": normalise_currency(df[ccy]),
                        "value_date": to_date(df[value_date]), "row": row})
    bad = out[COMPARE].isna().any(axis=1)
    if bad.any():
        raise BusinessError(f"{side}: blank or unreadable amount, currency or date", code="UNREADABLE", side=side,
                            rows=out.loc[bad, "row"].tolist()[:20])
    dup = out["key"].duplicated(keep=False)
    if dup.any():
        raise BusinessError(f"{side}: a ref is on more than one row", code="DUPLICATE_KEYS", side=side,
                            refs=sorted(set(out.loc[dup, "key"]))[:20])
    return out


def summary(recon, rows_left, rows_right, tol_cents):
    """Rows per status (zeros included), the matches that used the tolerance, and each side's row count."""
    counts = recon_summary(recon).set_index("status")["count"].reindex(STATUSES, fill_value=0)
    used = int(((recon["status"] == "MATCHED") & (recon["amount_diff"] != 0)).sum())
    extra = {f"MATCHED within {tol_cents} cent tolerance": used, "rows left": rows_left, "rows right": rows_right}
    return pd.DataFrame({"status": [*counts.index, *extra], "count": [*counts.astype(int), *extra.values()]})


def break_cells(breaks):
    """Excel addresses of the differing fields on the Breaks sheet (both sides), for highlight_cells."""
    letter = {c: col_letter(i) for i, c in enumerate(breaks.columns, start=1)}
    return [f"{letter[c + s]}{r}" for r, cols in enumerate(breaks["break_cols"], start=2)
            for c in cols.split(",") for s in ("_l", "_r")]


def write_recon_report(recon, summary_df, path):
    """Summary / Breaks / Left only / Right only with differing cells yellow; built under a temp name, then renamed."""
    path = Path(path)
    part = path.with_name(f"~{path.stem}.part.xlsx")
    of = lambda s: recon[recon["status"] == s].reset_index(drop=True)
    breaks = of("BREAK")[BREAK_VIEW]
    sheets = {"Summary": summary_df, "Breaks": breaks, "Left only": of("LEFT_ONLY").filter(regex="^key$|_l$"),
              "Right only": of("RIGHT_ONLY").filter(regex="^key$|_r$")}
    try:
        write_report(part, sheets, number_formats=FORMATS)
        highlight_cells(part, "Breaks", break_cells(breaks))
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


def run_recon(left, left_cols, right, right_cols, path, tol_cents=TOL_CENTS, log=print):
    """Normalise both sides, reconcile, write the report; returns the recon frame (one row per ref, with status).
    left_cols / right_cols = the (ref, amount, ccy, value_date) column names of each side."""
    L, R = normalise(left, "L", *left_cols), normalise(right, "R", *right_cols)
    recon = reconcile(L, R, on="key", compare=COMPARE, tolerance={"amount": tol_cents / 100})
    write_recon_report(recon, summary(recon, len(L), len(R), tol_cents), path)
    log(f"recon {Path(path).name}: " + ", ".join(f"{s} {n}" for s, n in recon["status"].value_counts().items()))
    return recon


def demo(tmp):
    """Case, space, zero-padding, currency-alias and 0.004 rounding differences match; real breaks are reported with
    their cells yellow. A ref on two rows after normalising, or an unreadable amount, stops the run with no report."""
    from openpyxl import load_workbook
    ledger = pd.DataFrame({"Ref": ["123", "ab-77", " 0045", "9001", "9002", "9003", ""],
                           "Amount": ["1,000.00", "250.50", "(75.00)", "500.00", "10.00", "20.00", "5.00"],
                           "Ccy": ["usd", "S$", "EUR", "USD", "USD", "USD", "SGD"], "Value Date": "06/03/2026"})
    bank = pd.DataFrame({"reference": ["0000000123", "AB77 ", "45", "9001", "9002", "9100"],
                         "amt": [1000.004, 250.5, -75.0, 505.0, 10.0, 7.5],
                         "currency": ["USD", "SGD", "eur", "USD", "USD", "USD"],
                         "date": ["2026-03-06", "6/3/2026", "2026-03-06", "2026-03-06", "2026-03-09", "2026-03-06"]})
    cols_l, cols_r, lines = ("Ref", "Amount", "Ccy", "Value Date"), ("reference", "amt", "currency", "date"), []
    st = run_recon(ledger, cols_l, bank, cols_r, tmp / "recon.xlsx", log=lines.append).set_index("key")["status"]
    assert set(st[st == "MATCHED"].index) == {"0000000123", "000000AB77", "0000000045"}
    assert st["0000009001"] == st["0000009002"] == "BREAK" and st["0000009100"] == "RIGHT_ONLY"
    assert st["0000009003"] == st["NOREF-L-8"] == "LEFT_ONLY" and "9001" not in lines[0]

    wb = load_workbook(tmp / "recon.xlsx")
    assert wb.sheetnames == ["Summary", "Breaks", "Left only", "Right only"]
    assert dict(wb["Summary"].iter_rows(min_row=2, values_only=True)) == {
        "MATCHED": 3, "BREAK": 2, "LEFT_ONLY": 2, "RIGHT_ONLY": 1, "MATCHED within 1 cent tolerance": 1,
        "rows left": 7, "rows right": 6}
    br = wb["Breaks"]                                     # row 2 = 9001 (amount differs), row 3 = 9002 (date)
    assert br["C2"].fill.fill_type == br["D2"].fill.fill_type == br["H3"].fill.fill_type == "solid"
    assert br["F2"].fill.fill_type is None and br["C3"].fill.fill_type is None

    duplicate = pd.concat([ledger, ledger[:1].assign(Ref="0123")], ignore_index=True)
    unreadable = ledger.assign(Amount=ledger["Amount"].replace("500.00", "5OO.00"))
    for bad, code, context in ((duplicate, "DUPLICATE_KEYS", {"refs": ["0000000123"]}),
                               (unreadable, "UNREADABLE", {"rows": [5]})):
        try:
            run_recon(bad, cols_l, bank, cols_r, tmp / "stopped.xlsx", log=lines.append)
        except BusinessError as e:
            assert e.code == code and context.items() <= e.context.items() and not (tmp / "stopped.xlsx").exists()
        else:
            raise AssertionError(f"expected {code}")
