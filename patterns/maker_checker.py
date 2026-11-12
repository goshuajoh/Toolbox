"""
title: Maker-checker - a named person approves, the robot executes
order: 90
when: "Anything irreversible or customer-facing (release a payment, amend a trade, write off a fee): the robot prepares the actions, a named checker approves or rejects each one in Excel, and the robot executes only what was approved."
tags: [approval, submit, controls]
needs: []
blocks: [RETRY_PROFILES, BusinessError, Checkpoint, RobotError, SystemError_, attempt, file_sha256, get_secret, process_all, propose, read_decisions, safe_write_excel]
decisions:
  - "Two runs, so no machine waits on a person: propose (write the workbook, tell the checker, exit 0; a re-run that finds the record leaves the round alone), then execute, scheduled every 15 min. Until every row is decided or the deadline (4 h after the proposal) has passed, execute stops with SystemError_ AWAITING_CHECKER (exit 3, run again later) and does nothing. APPROVED without a checker id is not decided yet. Then the round closes: PENDING rows become EXPIRED, APPROVED without an id NO_CHECKER_ID; propose them again on fresh data. The workbook does not record when a cell changed, so a decision counts if it is there when the robot closes the round: schedule one execute run at the deadline itself."
  - "The robot keeps its own record of each proposal (fingerprint per action_id, maker, deadline) and executes a row only if the workbook still matches it. toolbank's fingerprint covers the id, the action and every proposed value: read_decisions checks the row against it, the record checks it against the proposal. So a changed value or action, an added row or a fingerprint typed to match an edit is TAMPERED, and a copied row is DUPLICATE (both copies). read_decisions(maker=, approvers=) is the four-eyes check: a row executes only if it is APPROVED, has a checker id, that id is neither the maker's nor the robot's (SAME_PERSON) and it is on the approvers list (NOT_AUTHORISED). The approved list is frozen in the record at the close: a later approval is AFTER_CLOSE and never executes."
  - "propose_round reads the workbook back at once. If a row does not read back as proposed (text starting with '=' is written as a formula), it stops with RobotError ROUND_TRIP (exit 5) and writes no record, rather than send the checker a round that cannot be approved. Ids and text of digits (account 00123) come back as text."
  - "An action_id is sent at most once, ever. already_done(action_id) asks the target first, outside attempt: True or False, and it raises when it cannot tell, which stops the row instead of sending blind. The id goes into sent.json just before send(); send() gets the row's proposed values, blanks left out. Then RETRY_PROFILES['submit']: 1 try, confirmed when already_done shows it within 30 s (asked every 5 s)."
  - "Not confirmed, a timeout, or a KeyError / ValueError after the send (the reply did not parse) is SystemError_ SUBMIT_UNCONFIRMED for that row, now and on every later run, and it is not sent again. attempt alone raises STEP_UNCONFIRMED, which stops the whole run; sent.json already holds the id, so the block makes it a row failure and the other rows go on. A bug or a non-retryable error (AUTH) in send() still stops the run, the id held. A refusal (a BusinessError raised in send(): raise it only when the target said no) goes into refused.json and is REFUSED from then on: propose it again under a new action_id. api_post's NOT_JSON comes after a 2xx, so it is held, not a refusal. process_all stops after 3 system failures in a row. Held rows count too, so a re-run that meets three held rows in a row stops there until the target shows them or a person clears them."
governance:
  - "Evidence per round: action, decision, checker id, comment, fingerprint, check result and outcome of every proposal, plus maker, deadline, close time and the SHA-256 of the workbook at the close. Logs carry action ids and codes, never amounts or account numbers. attempt and process_all redact their log lines (national ids, emails, IBANs, long numbers) but still log send()'s error text, so send() raises its own short code and the id."
  - "The sheet is protected (password from get_secret): only decision, checker and comment can be edited, the decision from a dropdown. Protection stops slips, not a determined user, so the robot's own record is the real check: report every TAMPERED and DUPLICATE row to the control owner. A checker id typed in a cell is a claim, not a login: keep the workbook in a folder only the approvers can write to, or approve in a workflow tool with logins where policy needs proof of identity."
  - "Keep the round records, sent.json and refused.json outside the dated run folder (a round can cross midnight), e.g. work/<robot>/approvals/, where checkers cannot write. Run one copy of the robot at a time across dates too (the template's single_instance(ROBOT, lock_dir=rd.parent) does): Checkpoint rewrites the whole file, so two runs at once can drop an id from sent.json or send one twice. Only a person takes an id out of sent.json (Checkpoint(path).discard(action_id)), after checking the target: that edit is the approval to send it again. The approvers list is configuration the process owner signs off."
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from toolbank import (RETRY_PROFILES, BusinessError, Checkpoint, RobotError, SystemError_, attempt, file_sha256,
                      get_secret, process_all, propose, read_decisions, safe_write_excel)


def propose_round(book, record, actions, maker, now, hours=4):
    """Maker: the protected workbook for the checker, who has `hours` to decide, and the robot's own record of it."""
    if Path(record).exists():
        return                                            # a re-run must not wipe the checker's decisions
    d = read_decisions(propose(book, actions, protect_password=get_secret("APPROVAL_SHEET_PASSWORD")))
    if bad := d.loc[d["why"] != "NOT_APPROVED", "action_id"].tolist():   # e.g. text '=...' became a formula
        raise RobotError("proposed values change in the Excel round trip", code="ROUND_TRIP", action_ids=bad)
    rec = {"maker": maker, "deadline": (now + timedelta(hours=hours)).isoformat(),
           "actions": dict(zip(d["action_id"], d["fingerprint"]))}
    Path(record).write_text(json.dumps(rec, indent=1), encoding="utf-8")


def why_not(r, rec, late):
    """'OK' if the robot may execute this workbook row, else why not: read_decisions' why, the record, the deadline."""
    if r["why"] == "DUPLICATE":                           # a copied row: neither copy executes
        return "DUPLICATE"
    if r["why"] == "TAMPERED" or rec["actions"].get(r["action_id"]) != r["fingerprint"]:
        return "TAMPERED"                                 # edited, added, or a fingerprint typed to match an edit
    if r["why"] == "NOT_APPROVED":
        return "REJECTED" if str(r["decision"]).strip().upper() == "REJECTED" else "EXPIRED" if late else "PENDING"
    return "PENDING" if r["why"] == "NO_CHECKER_ID" and not late else r["why"]   # the checker may still type it


def check_round(book, record, robot_id, approvers, now):
    """(decisions with 'why' and 'ok', record) once the round is closed; SystemError_ AWAITING_CHECKER before."""
    rec = json.loads(Path(record).read_text(encoding="utf-8"))
    d = read_decisions(book, maker=[rec["maker"], robot_id], approvers=list(approvers))   # None fails: never off
    late = now >= datetime.fromisoformat(rec["deadline"])
    d["why"] = [why_not(r, rec, late) for _, r in d.iterrows()]
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


def unconfirmed(ref):
    """The error for a send that may have arrived but does not show in the target: held, never sent again."""
    return SystemError_("sent but not confirmed: check the target", code="SUBMIT_UNCONFIRMED", ref=ref)


def execute_approved(d, folder, send, already_done, log=print):
    """send(action, action_id, values) at most once per approved action_id, ever -> {action_id: outcome}."""
    sent, refused, outcome = Checkpoint(Path(folder) / "sent.json"), Checkpoint(Path(folder) / "refused.json"), {}

    def run(r):
        ref = r["action_id"]
        if already_done(ref):                             # outside attempt: a failed lookup stops the row
            outcome[ref] = "already done"
            return
        if sent.is_done(ref):                             # sent before and not in the target: never again
            if refused.is_done(ref):
                raise BusinessError("the target refused it", code="REFUSED", ref=ref)
            raise unconfirmed(ref)
        values = {k[len("proposed_"):]: v for k, v in r.items() if str(k).startswith("proposed_") and not pd.isna(v)}
        said_no = []

        def go():
            try:
                return send(r["action"], ref, values)
            except BusinessError as e:                    # api_post's NOT_JSON comes after a 2xx: not a 'no'
                said_no.extend([] if e.code == "NOT_JSON" else [ref])
                raise
        refused.discard(ref)                              # a person cleared it from sent.json: this send decides
        sent.mark_done(ref)                               # before the send: a timeout or a crash cannot resend
        try:
            attempt(go, verify=lambda: already_done(ref), name=f"execute {ref}", log=log, **RETRY_PROFILES["submit"])
        except RobotError as e:
            if said_no:
                refused.mark_done(ref)                    # the target said no: a definite 'not sent'
            elif isinstance(e, BusinessError) or e.code == "STEP_UNCONFIRMED":
                raise unconfirmed(ref) from e             # not confirmed, or the reply did not parse: held
            raise
        outcome[ref] = "executed"
    _, failures = process_all([r for _, r in d[d["ok"]].iterrows()], run, key=lambda r: r["action_id"], log=log)
    return outcome | dict(zip(failures["item"], failures["code"]))


def save_evidence(d, rec, outcome, path):
    """Who approved what: every proposal with decision, checker, check result and outcome, and the round itself."""
    rows = d[["action_id", "action", "decision", "checker", "comment", "fingerprint", "why"]]
    rnd = pd.DataFrame([{"maker": rec["maker"], "deadline": rec["deadline"], **rec["closed"]}]).drop(columns="approved")
    return safe_write_excel(path, {"Decisions": rows.assign(outcome=rows["action_id"].map(outcome).fillna("-")),
                                   "Round": rnd}, overwrite=True)


# --- demo ---


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0
    def clock(self): return self.now
    def sleep(self, s): self.now += s


def demo(tmp):
    """Ten proposals, one round: only the approved, untouched rows execute, never twice, nothing after the close."""
    from functools import partial
    from unittest import mock

    from openpyxl import load_workbook
    t0, t, target, calls, book = datetime(2026, 3, 6, 10, 0), FakeTime(), {}, [], tmp / "round.xlsx"
    acts = [{"action_id": f"PAY-{i}", "action": "RELEASE", "account": "00123", "amount": 1e3} for i in range(1, 11)]
    new_round = lambda: propose_round(book, tmp / "round.json", acts, "ana.maker", t0)
    check = lambda h: check_round(book, tmp / "round.json", "svc_payrobot", ["ben.checker"], t0 + timedelta(hours=h))
    run = lambda d: execute_approved(d, tmp, send, target.__contains__, log=[].append)

    def send(action, ref, values):                        # the target loses PAY-7 and refuses PAY-8
        calls.append(ref)
        if ref == "PAY-8":
            raise BusinessError("refused by the target", code="TARGET_REFUSED", ref=ref)
        target.update({} if ref == "PAY-7" else {ref: values})
    def checker(edits, copy_row=None):                    # the checker in Excel: {action_id: {column: new value}}
        wb = load_workbook(book)
        ws = wb["Approvals"]
        for row in ws.iter_rows(min_row=2):
            for k, v in edits.get(row[0].value, {}).items():
                row[[c.value for c in ws[1]].index(k)].value = v
        if copy_row:                                      # a row copied and pasted below the others
            ws.append([c.value for c in ws[copy_row]])
        wb.save(book)

    fakes = {"attempt": partial(attempt, sleep=t.sleep, clock=t.clock), "get_secret": lambda name: "demo-only"}
    with mock.patch.dict(globals(), fakes):               # no real waits, no vault; restored afterwards
        new_round()
        checker({"PAY-1": {"decision": "APPROVED"}})      # saved before the checker typed their id: not decided
        try:
            check(1)
            raise AssertionError("expected AWAITING_CHECKER")
        except SystemError_ as e:
            assert e.code == "AWAITING_CHECKER" and e.context["pending"] == 10
        ok = {"decision": "APPROVED", "checker": "Ben.Checker"}
        checker({"PAY-1": ok, "PAY-2": dict(ok, decision="REJECTED"), "PAY-3": dict(ok, proposed_amount=9000.0),
                 "PAY-4": dict(ok, checker="svc_payrobot"), "PAY-5": dict(ok, action="RETURN"), "PAY-7": ok,
                 "PAY-8": ok, "PAY-9": dict(ok, checker="eve.intern"), "PAY-10": ok}, copy_row=11)
        new_round()                                       # the propose step run again: the decisions survive
        d, rec = check(4.1)                               # just after the 4 h deadline: PAY-6 expires
        assert d["why"].tolist() == ["OK", "REJECTED", "TAMPERED", "SAME_PERSON", "TAMPERED", "EXPIRED", "OK", "OK",
                                     "NOT_AUTHORISED", "DUPLICATE", "DUPLICATE"]
        ev = pd.read_excel(save_evidence(d, rec, run(d), tmp / "approval_evidence.xlsx"), sheet_name=None)
        assert ev["Decisions"]["outcome"].tolist() == \
            ["executed"] + ["-"] * 5 + ["SUBMIT_UNCONFIRMED", "TARGET_REFUSED"] + ["-"] * 3   # PAY-8 still ran
        assert ev["Round"]["workbook_sha256"][0] == rec["closed"]["workbook_sha256"] and t.now >= 30
        assert target["PAY-1"] == {"account": "00123", "amount": 1000}                # '00123' stays text
        checker({"PAY-2": {"decision": "APPROVED"}})      # approved after the close: frozen out
        d, _ = check(5)
        assert d.set_index("action_id")["why"]["PAY-2"] == "AFTER_CLOSE"
        assert run(d) == {"PAY-1": "already done", "PAY-7": "SUBMIT_UNCONFIRMED", "PAY-8": "REFUSED"}
        assert calls == ["PAY-1", "PAY-7", "PAY-8"] and list(target) == ["PAY-1"]   # the re-run sent nothing
        garbled = lambda action, ref, values: int("OK, queued")   # the reply does not parse, after the send
        assert execute_approved(d.head(1), tmp / "other", garbled, lambda ref: False, log=[].append) == \
            {"PAY-1": "SUBMIT_UNCONFIRMED"}
        assert not Checkpoint(tmp / "other" / "refused.json").is_done("PAY-1")   # held, not refused
