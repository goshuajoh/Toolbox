"""
title: Maker-checker - a named person approves, the robot executes
order: 90
when: "Anything irreversible or customer-facing (release a payment, amend a trade, write off a fee): the robot prepares the actions, a named checker approves or rejects each one in Excel, and the robot executes only what was approved."
tags: [maker-checker, approval, segregation-of-duties, excel, evidence, idempotent]
needs: []
blocks: [RETRY_PROFILES, SystemError_, attempt, file_sha256, get_secret, process_all, propose, read_decisions, safe_write_excel]
decisions:
  - "Two runs, so no machine waits on a person: propose (write the workbook, tell the checker, exit 0; a re-run that finds the record leaves the round alone), then execute, scheduled every 15 min."
  - "Until every row is decided or the deadline (4 h after the proposal) has passed, execute stops with SystemError_ AWAITING_CHECKER (exit 3, run again later) and does nothing. Then the round closes and PENDING rows become EXPIRED: propose them again on fresh data. The workbook does not record when a cell changed, so a decision counts if it is there when the robot closes the round: schedule one execute run at the deadline itself."
  - "The robot keeps its own record of each proposal (action and fingerprint per action_id, maker, deadline) in its run folder and executes a row only if the workbook still matches it. A changed value, a changed action or an added row is TAMPERED. toolbank's fingerprint covers the proposed_ values but not the action column; the record covers both."
  - "A row executes only if it is APPROVED, has a checker id, that id is neither the maker's nor the robot's (SAME_PERSON) and it is on the approvers list (NOT_AUTHORISED). The approved list is frozen in the record at the close: a later approval is AFTER_CLOSE and never executes."
  - "Each approved row is sent once. already_done(action_id) asks the target system first, outside attempt, so a failed lookup stops the row instead of sending blind. Then RETRY_PROFILES['submit']: 1 try, confirmed by already_done within 30 s, else STEP_FAILED for that row. A re-run re-checks the row and skips what the target already has. If the target can take longer than 30 s to show a send, add the hold list of the idempotent_submit pattern. A refused or failed row never stops the others; process_all stops after 3 failed sends in a row."
governance:
  - "Evidence per round: action, decision, checker id, comment, check result and outcome of every proposal, plus maker, deadline, close time and the SHA-256 of the workbook as read at the close. Logs carry action ids, checker ids and codes, never amounts or account numbers."
  - "The sheet is protected (password from get_secret): only decision, checker and comment can be edited, the decision from a dropdown. Protection stops slips, not a determined user, so the robot's own record is the real check. Report every TAMPERED row to the control owner."
  - "A checker id typed in a cell is a claim, not a login: keep the workbook in a folder only the approvers can write to, or approve in a workflow tool with logins where policy needs proof of identity. The approvers list is configuration the process owner signs off."
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from toolbank import (RETRY_PROFILES, SystemError_, attempt, file_sha256, get_secret, process_all, propose,
                      read_decisions, safe_write_excel)

ROUND_HOURS = 4                                           # the checker's window; PENDING rows expire after it


def propose_round(book, record, actions, maker, now, secret=get_secret):
    """Maker: the protected workbook for the checker, and the robot's own record of what it proposed."""
    if Path(record).exists():
        return                                            # a re-run must not wipe the checker's decisions
    propose(book, actions, protect_password=secret("APPROVAL_SHEET_PASSWORD"))
    rec = {"maker": maker, "deadline": (now + timedelta(hours=ROUND_HOURS)).isoformat(),
           "actions": {r.action_id: [r.action, r.fingerprint] for r in read_decisions(book).itertuples()}}
    Path(record).write_text(json.dumps(rec, indent=1), encoding="utf-8")


def why_not(r, rec, me, approvers, late):
    """'OK' if the robot may execute this workbook row, else why not."""
    if rec["actions"].get(r["action_id"]) != [r["action"], r["fingerprint"]] or r["why"] == "TAMPERED":
        return "TAMPERED"
    decision = str(r["decision"]).strip().upper()
    if decision != "APPROVED":
        return "REJECTED" if decision == "REJECTED" else "EXPIRED" if late else "PENDING"
    if r["why"] != "OK":
        return r["why"]                                   # NO_CHECKER_ID
    checker = str(r["checker"]).strip().casefold()
    return "SAME_PERSON" if checker in me else "OK" if checker in approvers else "NOT_AUTHORISED"


def check_round(book, record, robot_id, approvers, now):
    """(decisions with 'why' and 'ok', record) once the round is closed; SystemError_ AWAITING_CHECKER before."""
    rec = json.loads(Path(record).read_text(encoding="utf-8"))
    me = {rec["maker"].casefold(), robot_id.casefold()}
    allowed, late = {a.strip().casefold() for a in approvers}, now >= datetime.fromisoformat(rec["deadline"])
    d = read_decisions(book)
    d["why"] = [why_not(r, rec, me, allowed, late) for _, r in d.iterrows()]
    if gone := sorted(set(rec["actions"]) - set(d["action_id"])):                # a row deleted from the sheet
        d = pd.concat([d, pd.DataFrame({"action_id": gone, "why": "MISSING"})], ignore_index=True)
    if "closed" not in rec:
        if pending := int((d["why"] == "PENDING").sum()):
            raise SystemError_("the checker has not decided every row yet", code="AWAITING_CHECKER", pending=pending)
        rec["closed"] = {"closed_at": now.isoformat(), "workbook_sha256": file_sha256(book),
                         "approved": d.loc[d["why"] == "OK", "action_id"].tolist()}
        Path(record).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    d.loc[d["why"].eq("OK") & ~d["action_id"].isin(rec["closed"]["approved"]), "why"] = "AFTER_CLOSE"
    return d.assign(ok=d["why"] == "OK"), rec


def execute_approved(d, send, already_done, log=print, sleep=time.sleep, clock=time.monotonic):
    """send(action, action_id, values) once per approved row -> {action_id: outcome}; already_done asks the target."""
    outcome = {}
    def run(r):
        ref, done = r["action_id"], already_done(r["action_id"])       # outside attempt: an error stops the row
        if not done:
            values = {k[len("proposed_"):]: v for k, v in r.items() if k.startswith("proposed_")}
            attempt(lambda: send(r["action"], ref, values), verify=lambda: already_done(ref), name=f"execute {ref}",
                    log=log, sleep=sleep, clock=clock, **RETRY_PROFILES["submit"])
        outcome[ref] = "already done" if done else "executed"

    _, failures = process_all([r for _, r in d[d["ok"]].iterrows()], run, key=lambda r: r["action_id"], log=log)
    return outcome | dict(zip(failures["item"], failures["code"]))


def save_evidence(d, rec, outcome, path):
    """Who approved what: every proposal with decision, checker, check result and outcome, and the round itself."""
    rows = d[["action_id", "action", "decision", "checker", "comment", "why"]]
    rnd = pd.DataFrame([{"maker": rec["maker"], "deadline": rec["deadline"], **rec["closed"]}]).drop(columns="approved")
    return safe_write_excel(path, {"Decisions": rows.assign(outcome=rows["action_id"].map(outcome).fillna("-")),
                                   "Round": rnd}, overwrite=True)


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


def demo(tmp):
    """Round 1: approved, rejected, approved after the amount was edited: only the first executes, once.
    Round 2, read after the deadline: self-approval, changed action, pending, and a send the target loses."""
    from openpyxl import load_workbook
    t0, t, target, ok = datetime(2026, 3, 6, 10, 0), FakeTime(), {}, {"decision": "APPROVED", "checker": "Ben.Checker"}
    run = lambda d, send=lambda a, ref, v: target.setdefault(ref, v): execute_approved(
        d, send, target.__contains__, log=[].append, sleep=t.sleep, clock=t.clock)

    def new_round(name, ids):
        actions = [{"action_id": i, "action": "RELEASE", "account": "00123-4455", "amount": 1000.0} for i in ids]
        propose_round(tmp / f"{name}.xlsx", tmp / f"{name}.json", actions, "ana.maker", t0, secret=lambda n: "vault")
        return lambda now: check_round(tmp / f"{name}.xlsx", tmp / f"{name}.json", "svc_payrobot", ["ben.checker"], now)

    def checker(name, edits):                             # the checker in Excel: {action_id: {column: new value}}
        wb = load_workbook(tmp / f"{name}.xlsx")
        ws, col = wb["Approvals"], {c.value: c.column for c in wb["Approvals"][1]}
        for r in range(2, ws.max_row + 1):
            for k, v in edits.get(ws.cell(r, 1).value, {}).items():
                ws.cell(r, col[k]).value = v
        wb.save(tmp / f"{name}.xlsx")

    check = new_round("r1", ["PAY-1", "PAY-2", "PAY-3"])
    assert load_workbook(tmp / "r1.xlsx")["Approvals"].protection.sheet
    try:
        check(t0 + timedelta(hours=1))
        raise AssertionError("expected AWAITING_CHECKER")
    except SystemError_ as e:
        assert e.code == "AWAITING_CHECKER" and e.context["pending"] == 3
    checker("r1", {"PAY-1": ok, "PAY-2": dict(ok, decision="REJECTED"), "PAY-3": dict(ok, proposed_amount=9000.0)})
    check = new_round("r1", ["PAY-1", "PAY-2", "PAY-3"])          # the propose step run again: decisions survive
    d, rec = check(t0 + timedelta(hours=2))
    assert dict(zip(d["action_id"], d["why"])) == {"PAY-1": "OK", "PAY-2": "REJECTED", "PAY-3": "TAMPERED"}
    ev = pd.read_excel(save_evidence(d, rec, run(d), tmp / "approval_evidence.xlsx"), sheet_name=None)
    assert ev["Decisions"]["outcome"].tolist() == ["executed", "-", "-"] and list(target) == ["PAY-1"]
    assert ev["Round"]["workbook_sha256"][0] == rec["closed"]["workbook_sha256"] and target["PAY-1"]["amount"] == 1000
    checker("r1", {"PAY-2": {"decision": "APPROVED"}})   # approved after the close: frozen out, re-run sends nothing
    d, _ = check(t0 + timedelta(hours=3))
    assert d.set_index("action_id")["why"]["PAY-2"] == "AFTER_CLOSE" and run(d) == {"PAY-1": "already done"}
    check = new_round("r2", ["PAY-4", "PAY-5", "PAY-6", "PAY-7"])
    checker("r2", {"PAY-4": dict(ok, checker="svc_payrobot"), "PAY-5": dict(ok, action="RETURN"), "PAY-7": ok})
    d, _ = check(t0 + timedelta(hours=ROUND_HOURS, minutes=5))
    assert d["why"].tolist() == ["SAME_PERSON", "TAMPERED", "EXPIRED", "OK"]
    assert run(d, send=lambda *a: None) == {"PAY-7": "STEP_FAILED"} and list(target) == ["PAY-1"] and t.now >= 30
