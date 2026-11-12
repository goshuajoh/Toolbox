"""
title: Work through items with exceptions, resume and a stop rule
order: 50
when: >-
  Any robot that works a queue one item at a time (payments, cases, trades, rows of a file), where one bad item
  must not stop the rest and a crash or an outage must not repeat work already done.
tags: [item-loop, exceptions, checkpoint, resume, exit-codes, report]
needs: []
blocks: [BusinessError, Checkpoint, SystemError_, cents, get_logger, process_all, progress, safe_write_excel, synth]
decisions:
  - "In a finished run every item ends in one state: done (in the checkpoint), business exception or system failure, each failure a row in the report. A run that stops (SYSTEM_STREAK, a bug) leaves the rest for the re-run."
  - "Checkpoint key = the business reference (payment id), never the row number: a re-sorted or re-sent file still resumes right. A blank or repeated reference is reported as MISSING_KEY / DUPLICATE_KEY, not skipped."
  - "Wrong data raises BusinessError with a short code the owner can act on (MISSING_ACCOUNT, MISSING_AMOUNT, NOT_IN_LEDGER, AMOUNT_MISMATCH): recorded, never retried, the loop goes on."
  - "A failing environment raises SystemError_ with its own code (LEDGER_DOWN) or is a plain OSError / TimeoutError, which process_all treats the same: recorded as retryable and left for the re-run, not retried in this run. If a read must succeed in the same run, wrap it in attempt with RETRY_PROFILES['read'] inside the handler."
  - "Stop after 3 system failures in a row (max_consecutive_system=3): one or two blips do not stop a long queue, and a system that is down stops the run after 3 items instead of timing out on every one. A success or a business exception resets the count, so a system that fails every other item never trips it: watch the retryable count."
  - "A bug (TypeError, AttributeError) stops the run at once (exit 5): every item would fail the same way. A KeyError or ValueError counts as bad data (BAD_DATA), so check the fields you need and raise your own code."
  - "Exit code from the failures: 3 if any is retryable (re-run, the checkpoint skips what is done), else 2 if any (send the report), else 0."
  - "The report is deleted at the start and written at the end of every run (safe_write_excel: temp file and rename). After the last re-run it lists exactly the items still open; a clean run writes an empty one, so 'no exceptions' is on file. A run stopped by SYSTEM_STREAK leaves no report: the log says why and the template exits 3."
  - "progress() logs every 100 items (count, %, rate, time left): 50 lines for a 5,000 item queue, enough to spot a slowdown."
  - "Money is compared in cents (cents), never as floats."
governance:
  - "Logs and the report carry the reference and the code, never values: write 'amount differs from the ledger', not the two amounts. process_all logs each failure message (log=log.warning); get_logger masks account-like numbers as a second line of defence."
  - "A library error message can hold a value (could not convert '1,234 USD'): check fields before a library sees them and raise your own BusinessError."
  - "ACTIONS (code -> what the owner does next) is part of the exception table the process owner signs in design: a new code needs their sign-off."
  - "The checkpoint holds references only. Keep it in the dated run folder (run_dir) with the log, under the same retention rule; delete it only to process a business date again on purpose."
"""
import pandas as pd

from toolbank import (BusinessError, Checkpoint, SystemError_, cents, get_logger, process_all, progress,
                      safe_write_excel, synth)

ACTIONS = {"MISSING_ACCOUNT": "Add the account number in the source file",      # signed off by the process owner
           "MISSING_AMOUNT": "Add the amount in the source file",
           "NOT_IN_LEDGER": "Check whether the payment was booked",
           "AMOUNT_MISMATCH": "Check the booking with the ledger team"}


def blank(value):
    """True for None, NaN and text that is only spaces."""
    return value is None or (not isinstance(value, str) and pd.isna(value)) or not str(value).strip()


def check_payment(item, ledger):
    """Business rules for one payment against ledger(ref) -> amount or None; bad data raises BusinessError."""
    ref = item["ref"]
    if blank(item.get("account")):
        raise BusinessError("account is blank", code="MISSING_ACCOUNT", ref=ref)
    if blank(item.get("amount")):
        raise BusinessError("amount is blank", code="MISSING_AMOUNT", ref=ref)
    booked = ledger(ref)                                  # OSError / SystemError_ here: left for the re-run
    if booked is None:
        raise BusinessError("reference not found in the ledger", code="NOT_IN_LEDGER", ref=ref)
    if cents(booked) != cents(item["amount"]):
        raise BusinessError("amount differs from the ledger", code="AMOUNT_MISMATCH", ref=ref)


def exit_code(failures):
    """3 if any failure is retryable (re-run resumes), 2 for business exceptions only, 0 when there are none."""
    return 3 if failures["retryable"].any() else 2 if len(failures) else 0


def write_exceptions(failures, path):
    """Exceptions report for the owner: reference, code, reason and the next action."""
    action = [("Re-run picks it up; tell support if it stays" if retry else ACTIONS.get(code, "Check the source data"))
              for code, retry in zip(failures["code"], failures["retryable"])]
    report = failures[["item", "code", "message", "retryable"]].assign(action=action)
    return safe_write_excel(path, {"Exceptions": report}, overwrite=True)


def run_items(items, handle, run_folder, log, every=100):
    """Work each open item once (items: dicts with a 'ref'), resuming from the checkpoint; returns the exit code."""
    report = run_folder / "exceptions.xlsx"
    report.unlink(missing_ok=True)                        # a report on file always belongs to a finished run
    cp = Checkpoint(run_folder / "checkpoint.json")
    queue = progress(items, every=every, label="items", log=log.info)
    _, failures = process_all(queue, handle, max_consecutive_system=3, checkpoint=cp,
                              key=lambda item: item.get("ref"), log=log.warning)
    write_exceptions(failures, report)
    log.info("items finished", extra={"event": "items", "rows": len(items), "done": len(cp.done),
                                      "count": len(failures)})
    return exit_code(failures)


def demo(tmp):
    """8 payments: a blank account, an amount 1 cent off the ledger, one ledger time-out. Run 1 exits 3, the re-run
    handles only the 3 open items and exits 2, the run after the data fix exits 0. A ledger that is down stops the
    loop after 3 items and marks nothing done."""
    df = synth(8, {"n": ("seq", 1001), "account": ("pattern", "999-99999-9"), "amount": ("number", 10, 5000)})
    items = [{"ref": f"PAY-{r.n}", "account": r.account, "amount": r.amount} for r in df.itertuples()]
    items[2]["account"] = " "                             # PAY-1003: bad data
    book = {item["ref"]: item["amount"] for item in items}
    book["PAY-1005"] += 0.01                              # PAY-1005: one cent off
    timeouts, outage, handled = {"PAY-1007"}, [], []

    def ledger(ref):
        if outage:
            raise SystemError_("ledger not answering", code="LEDGER_DOWN", ref=ref)
        if ref in timeouts:
            timeouts.discard(ref)
            raise OSError("ledger did not answer within 30 s")
        return book.get(ref)

    def handle(item):
        handled.append(item["ref"])
        check_payment(item, ledger)

    def run(folder):
        handled.clear()
        return run_items(items, handle, folder, log, every=4)

    log = get_logger(tmp / "run.log.jsonl", console=False)
    report = lambda: pd.read_excel(tmp / "exceptions.xlsx")
    assert run(tmp) == 3 and len(handled) == 8
    assert dict(zip(report()["item"], report()["code"])) == {
        "PAY-1003": "MISSING_ACCOUNT", "PAY-1005": "AMOUNT_MISMATCH", "PAY-1007": "SYSTEM"}
    assert run(tmp) == 2 and handled == ["PAY-1003", "PAY-1005", "PAY-1007"]     # resumes: open items only
    items[2]["account"], book["PAY-1005"] = "123-45678-9", items[4]["amount"]    # the owner fixes the data
    assert run(tmp) == 0 and handled == ["PAY-1003", "PAY-1005"] and report().empty

    outage.append(True)
    (tmp / "outage").mkdir()
    try:
        run(tmp / "outage")
    except SystemError_ as e:
        assert e.code == "SYSTEM_STREAK" and len(handled) == 3
        assert not (tmp / "outage" / "checkpoint.json").exists() and not (tmp / "outage" / "exceptions.xlsx").exists()
    else:
        raise AssertionError("expected SYSTEM_STREAK")
    text = (tmp / "run.log.jsonl").read_text(encoding="utf-8")
    assert "skip PAY-1003: [MISSING_ACCOUNT]" in text and "items 8/8 (100%)" in text and "system failure 3/3" in text
    assert str(items[4]["amount"]) not in text            # ids and codes in the log, never amounts
