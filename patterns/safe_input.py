"""
title: Accept an input file only when it is complete and right
order: 60
when: >-
  A robot that starts from a file another team or system drops in a folder (an export, a statement, a payment
  list): take it only when it is the business date's file, complete and closed, and reject it whole when it is wrong.
tags: [input, controls]
needs: []
blocks: [RETRY_PROFILES, BusinessError, MissingColumns, SystemError_, archive, assert_totals_match, assert_unique, attempt, check_schema, cleanse, control_totals, file_sha256, is_locked, read_any, standard_columns, to_date, to_number, wait_for_file]
decisions:
  - "The business date's file only: wait_for_file with min_mtime = midnight of that date, so yesterday's file left in the folder is never taken again. No file within 1 h (timeout_s=3600) is SystemError_ FILE_TIMEOUT: exit 3, re-run or chase the sender. One file per run: if two match, the newest is taken; for several files a day use watch_folder."
  - "Complete: the size must stay the same for 30 s (stable_s=30, checked every 10 s); a 0-byte file never counts as arrived. Closed: is_locked checked 5 times, 10 s apart and doubling (RETRY_PROFILES['file_lock'], 150 s in all), then SystemError_ STEP_FAILED, exit 3. is_locked opens the file for append, so the robot account needs modify rights on the inbox (archive needs them too)."
  - "Read every column as text (dtype=str), then convert only the amount and the value date (to_number, to_date): account numbers keep their leading zeros and long references do not turn into floats."
  - "Header variants go through ALIASES (standard_columns, which also forgives case, spaces and punctuation: 'VALUE_DATE'); then all four standard names must be there. read_any(required=) alone would reject another name ('Val Dt'), so it is not used here."
  - "Reject the whole file with a BusinessError and a code (exit 2, tell the owner): MISSING_COLUMNS (also two columns for one name), UNREADABLE (no header, an unknown file type, a broken or password-protected workbook), EMPTY_INPUT (no rows once wholly blank rows such as ',,,' are dropped), BAD_VALUES (a blank ref, or an amount or date that is blank or does not parse), DUPLICATE_KEY (assert_unique on ref trimmed and in capitals, checked after BAD_VALUES so two blank refs are not called duplicates), WRONG_TYPES, TOTALS_MISMATCH. One bad row usually means the export changed; processing the rest would hide that. A broken workbook raises zipfile.BadZipFile, which the template would call a bug (exit 5), so it is caught as UNREADABLE."
  - "check_schema is the last guard that the frame has the types the next steps assume. It cannot see a value that did not parse (to_number makes it NaN), so those are counted first and named by row number (header on row 1; empty lines of a CSV are not counted)."
  - "Archive only when every item reached a final state (exit 0 or 2) and the SHA-256 is unchanged; on exit 3 the file stays for the re-run. A rejected file has no evidence, so it is never archived: it stays in the inbox for the owner to replace."
governance:
  - "Evidence for every accepted file before any item is processed: name, size, SHA-256 and control totals (rows, sum, absolute sum, distinct refs); log it with extra=. The hash is taken before the read, so it names the bytes the robot used."
  - "If the sender gives a count and total (trailer row, control file, cover email), pass them as expected= with the keys rows and sum (abs_sum, distinct_keys also work; assert_totals_match ignores any other key): a difference is TOTALS_MISMATCH and nothing is processed."
  - "Rejection messages carry codes, column names, references and row numbers, never an item's amount or an account number; the owner opens the file to see the values. MISSING_COLUMNS names only the missing standard names, not the headers found: in a file without a header row those are the first row's values. TOTALS_MISMATCH shows the file totals, which the evidence holds anyway."
  - "ALIASES is configuration the process owner signs off: a new header spelling is a reviewed change, not a quiet code fix."
  - "The archive keeps the file as YYYYMMDD_name, the input evidence for audit, under the bank's retention rule. archive() keeps the day's first file of a name, so a second one stays in the inbox as ARCHIVE_EXISTS for a person to check."
"""
import os
import time
import zipfile
from datetime import date, datetime

import pandas as pd

from toolbank import (RETRY_PROFILES, BusinessError, MissingColumns, SystemError_, archive, assert_totals_match,
                      assert_unique, attempt, check_schema, cleanse, control_totals, file_sha256, is_locked, read_any,
                      standard_columns, to_date, to_number, wait_for_file)

ALIASES = {"ref": ["Reference", "Txn Ref", "Payment Ref"], "account": ["Account No", "Acct No", "Account Number"],
           "amount": ["Amt", "Amount (SGD)"], "value_date": ["Val Dt", "Value Dt"]}    # signed off by the owner
SCHEMA = {"ref": "text", "account": "text", "amount": "number", "value_date": "date"}


def wait_for_input(pattern, day=None, timeout_s=3600, stable_s=30, poll_s=10, locked=is_locked, log=print,
                   sleep=time.sleep):
    """The file of business date `day` (a date, today if None) once complete and closed; else SystemError_ (exit 3)."""
    since = datetime.combine(day or date.today(), datetime.min.time())
    path = wait_for_file(pattern, timeout_s=timeout_s, poll_s=poll_s, stable_s=stable_s, min_mtime=since)

    def closed():
        if locked(path):
            raise SystemError_("the input is open in another program", code="FILE_LOCKED", file=path.name)

    attempt(closed, name=f"open {path.name}", log=log, sleep=sleep, **RETRY_PROFILES["file_lock"])
    return path


def load_input(path, expected=None):
    """Read and check the whole file: (df, evidence), or BusinessError with a code for the owner (exit 2).
    expected: the sender's control totals, e.g. {"rows": 2, "sum": 950.5} from the trailer."""
    sha = file_sha256(path)                               # before the read: the evidence names the bytes used
    reject = lambda why, code, **ids: BusinessError(why, code=code, file=path.name, **ids)
    try:
        df = standard_columns(read_any(path, dtype=str), ALIASES)      # text: '00123' keeps its zeros
    except MissingColumns as e:                           # two columns for one name: 'Amount' and 'Amt'
        raise reject(str(e), "MISSING_COLUMNS") from None
    except (ValueError, zipfile.BadZipFile) as e:          # no header, unknown type, broken or protected workbook
        raise reject(str(e), "UNREADABLE") from None
    if missing := [c for c in SCHEMA if c not in df.columns]:     # not the headers found: they may be data
        raise reject(f"missing columns {missing}", "MISSING_COLUMNS")
    df = cleanse(df).dropna(how="all")                    # ',,,' is no row
    if df.empty:
        raise reject("the file has a header but no rows", "EMPTY_INPUT")
    df["amount"], df["value_date"] = to_number(df["amount"]), to_date(df["value_date"])
    bad = df["ref"].isna() | df["amount"].isna() | df["value_date"].isna()
    if bad.any():                                         # unparsable -> NaN, which check_schema cannot see
        raise reject(f"{int(bad.sum())} rows with a blank ref, amount or date, or one that does not parse",
                     "BAD_VALUES", rows=[int(i) + 2 for i in df.index[bad][:10]])
    try:
        assert_unique(df.assign(ref=df["ref"].str.upper()), "ref")     # 'pay-1' and 'PAY-1' are one reference
    except ValueError as e:
        raise reject(str(e), "DUPLICATE_KEY") from None
    if problems := check_schema(df, SCHEMA):
        raise reject("; ".join(problems), "WRONG_TYPES")
    totals = control_totals(df, "amount", key_col="ref")
    try:
        assert_totals_match(expected or {}, totals)
    except ValueError as e:
        raise reject(f"differs from the sender's control totals: {e}", "TOTALS_MISMATCH") from None
    return df, {"file": path.name, "bytes": path.stat().st_size, "sha256": sha, **totals}


def archive_if_final(path, evidence, exit_code, archive_dir):
    """After the run: archive the input when every item is final (exit 0 or 2) and the file is unchanged."""
    if exit_code not in (0, 2):
        return None                                       # exit 3: the re-run needs the file where it is
    if file_sha256(path) != evidence["sha256"]:
        raise BusinessError("the input changed during the run: not archived", code="INPUT_CHANGED", file=path.name)
    target = archive(path, archive_dir)
    if path.exists():                                     # archive() keeps the day's first file of this name
        raise BusinessError("a file of this name is already archived today", code="ARCHIVE_EXISTS", file=path.name)
    return target


def demo(tmp):
    """Today's file is taken, yesterday's is not; other headers give the same totals; a file kept open gives up
    after 150 s; empty, headerless, unreadable, duplicate and bad files are rejected by code; archive when final."""
    def expect(code, fn, *args, **kw):
        try:
            fn(*args, **kw)
        except (BusinessError, SystemError_) as e:
            assert e.code == code and "00123456" not in str(e), f"{code} without data expected, got {e}"
        else:
            raise AssertionError(f"{code} expected")

    head = "Reference,Account No,Amount,Value Date\n"
    rows = 'PAY-1,00123456,"1,250.50",12/11/2026\nPAY-2,00987654,(300.00),13/11/2026\n,,,\n'   # a blank row too
    for folder in ("inbox", "old"):
        (tmp / folder).mkdir()
        (tmp / folder / "payments.csv").write_text(head + rows)
    since = datetime.combine(date.today(), datetime.min.time()).timestamp()
    os.utime(tmp / "old" / "payments.csv", (since - 3600, since - 3600))     # last saved yesterday evening
    expect("FILE_TIMEOUT", wait_for_input, tmp / "old" / "*.csv", stable_s=0, poll_s=0, timeout_s=0.01)
    waits, log = [], []                                   # fake sleep: records the waits, takes no time
    expect("STEP_FAILED", wait_for_input, tmp / "inbox" / "*.csv", stable_s=0, poll_s=0, timeout_s=1,
           locked=lambda p: True, log=log.append, sleep=waits.append)
    assert waits == [10, 20, 40, 80] and len(log) == 5    # 5 tries, 150 s in all

    path = wait_for_input(tmp / "inbox" / "*.csv", stable_s=0, poll_s=0, timeout_s=1)
    df, ev = load_input(path, expected={"rows": 2, "sum": 950.5})
    assert (ev["rows"], ev["sum"], df.loc[0, "account"]) == (2, 950.5, "00123456")
    expect("TOTALS_MISMATCH", load_input, path, expected={"rows": 3})
    renamed = tmp / "renamed.xlsx"                        # the same data from another export, other headers
    pd.DataFrame({"Txn Ref": ["PAY-1", "PAY-2"], "Acct No": ["00123456", "00987654"], "Amt": [1250.5, -300],
                  "Val Dt": pd.to_datetime(["2026-11-12", "2026-11-13"])}).to_excel(renamed, index=False)
    ev2 = load_input(renamed)[1]
    assert all(ev2[k] == ev[k] for k in ("rows", "sum", "abs_sum", "distinct_keys"))

    bad = {"EMPTY_INPUT.csv": head + ",,,\n", "DUPLICATE_KEY.csv": head + rows + "pay-1 ,00555555,5,12/11/2026\n",
           "MISSING_COLUMNS.csv": rows, "UNREADABLE.pdf": "%PDF-1.7",              # no header row at all
           "BAD_VALUES.csv": head + "PAY-1,00123456,five,12/11/2026\n,00123456,5,12/11/2026\n,00987654,5,12/11/2026\n",
           "UNREADABLE.xlsx": head + rows}                # a CSV saved under an .xlsx name
    for name, text in bad.items():
        (tmp / name).write_text(text)
        expect(name.split(".")[0], load_input, tmp / name)

    assert archive_if_final(path, ev, 3, tmp / "archive") is None and path.exists()
    assert file_sha256(archive_if_final(path, ev, 0, tmp / "archive")) == ev["sha256"] and not path.exists()
    path.write_text(head + rows)                          # the same name delivered again the same day
    expect("ARCHIVE_EXISTS", archive_if_final, path, load_input(path)[1], 0, tmp / "archive")
    renamed.write_bytes(renamed.read_bytes() + b"\0")    # someone saves over the input during the run
    expect("INPUT_CHANGED", archive_if_final, renamed, ev2, 0, tmp / "archive")
    assert path.exists() and renamed.exists()
