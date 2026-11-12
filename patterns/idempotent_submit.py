"""
title: Submit, pay or send exactly once
order: 30
when: "Any step whose repeat would do harm: posting a payment, submitting an instruction or a case, sending a file to another team. One call per item, keyed by its business reference."
tags: [submit, retry, items]
needs: [requests]
blocks: [RETRY_PROFILES, BusinessError, Checkpoint, SystemError_, act, api_get, attempt, process_all]
decisions:
  - "Key every item by its business reference (payment id, instruction ref), trimmed, never a row number: rows move when a file is re-sorted, a reference does not. A blank one is BusinessError MISSING_REF. Key process_all(checkpoint=...) by the same trimmed reference, so one repeated in the input is reported (DUPLICATE_KEY), not returned as 'skipped'."
  - "Look the reference up first, outside attempt: found clears a hold and returns 'already there'; an error stops the item before anything is sent. lookup(ref) raises when it cannot search and is True only for an exact match: rest_lookup compares trimmed text, case counts (a wrong 'found' would drop a payment silently, a wrong 'not found' only holds it), 404 is not there, other 4xx is BusinessError."
  - "prepare() (open the form, fill it) runs first as its own screen step (RETRY_PROFILES['ui'], up to 3 tries). send() (press Submit, POST) is the point of no return: RETRY_PROFILES['submit'] (irreversible) with tries raised to 2, which attempt allows only because already_done is given. The second try never sends: it looks the reference up once more, 2 s after the first try failed."
  - "Confirmed means the lookup finds the reference within 30 s, polled every 5 s (RETRY_PROFILES['submit']: a search, not a screen check), not that send() returned. The checkpoint (its own file, submitted.json) is marked right after, so later steps of the item can fail and re-run without a resend."
  - "The reference goes on the hold list (unconfirmed.json) just before send() and comes off when the lookup finds it or the target refuses it. Anything else after that point leaves it held, never checkpointed: a timeout, a slow back-end, a reply that will not parse, a lookup that fails, a crash, Ctrl+C, a 401, a bug. That is SystemError_ SUBMIT_UNCONFIRMED (at once, or at the next run when the run stopped), and every later run raises it again without sending."
  - "A refusal (BusinessError from send(), e.g. HTTP 400) is a definite 'not sent': off the hold list, recorded as a business exception, not retried in this run. Raise BusinessError in send() only when the target said no. A KeyError or ValueError inside send() (attempt's BAD_DATA) and api_post's NOT_JSON are not a refusal: they can come after the target took the item, so it stays held. Build the request before submit_once, so bad data fails before the hold."
  - "process_all stops after 3 system failures in a row, so an outage holds a few references, not the whole batch (a business exception or a success in between restarts the count); a bug or a 401 stops the run at once. Until they are found or cleared, a re-run that meets them in a row stops there again."
governance:
  - "The robot takes a reference off unconfirmed.json only when the lookup finds it or the target refuses it. Any other removal is a person's, after checking the target system (Checkpoint(path).discard(ref)): that edit is the approval to send it again."
  - "Keep unconfirmed.json outside the dated run folder (e.g. work/<robot>/unconfirmed.json) so a hold survives into the next business date, and run one copy of the robot at a time across dates too (the template's single_instance(ROBOT, lock_dir=rd.parent) does): Checkpoint rewrites the whole file, so two runs writing it at once can lose a hold. The checkpoint covers one business date; the lookup in the target system is the only guard across machines."
  - "For an API use http_session() (it never resends a POST) and send the reference as an idempotency key if the API accepts one."
  - "This block's own errors carry the reference only. attempt redacts its log lines, but only ids, emails and long numbers: the target's error text (an HTTP 400 or 503 body) in a log line, a refusal or the error chain can still echo names or amounts. In send(), catch it and raise your own error of the same kind with a short code, from None (a BusinessError there would count as a refusal and release the hold)."
  - "Rehearse with dry_run=True before the first live run: it runs the lookup and prepare(), logs each send through act instead of making it and writes neither file; the returned status shows what is already there. If the target saves drafts, skip prepare() in a dry run."
"""
import pandas as pd

from toolbank import RETRY_PROFILES, BusinessError, SystemError_, act, api_get, attempt

# In the template's run() (rd = the dated run folder, lookup = e.g. rest_lookup(...), to_request / post = yours):
#   done, held = Checkpoint(rd / "submitted.json"), Checkpoint(rd.parent / "unconfirmed.json")   # held: all dates
#   def handle(row):
#       body = to_request(row)                    # bad data fails here, before anything is held
#       status = submit_once(row["ref"], lambda: post(body), lookup, done, held, dry_run=args.dry_run, log=log.warning)
#       results.append({"ref": str(row["ref"]).strip(), "status": status})    # submitted, already there, skipped...
#   process_all(rows, handle, checkpoint=cp, key=lambda row: str(row["ref"]).strip(), log=log.warning)


def unconfirmed(ref):
    """The error for a send that may have arrived but does not show in the target system."""
    return SystemError_("sent but not confirmed: check the target system before it is sent again",
                        code="SUBMIT_UNCONFIRMED", ref=ref)


def submit_once(ref, send, lookup, done, held, *, prepare=None, dry_run=False, log=print):
    """Send one item once (done, held: Checkpoint); returns 'skipped', 'already there', 'dry run' or 'submitted'."""
    if pd.isna(ref) or not str(ref).strip():
        raise BusinessError("no business reference: cannot check whether it was sent", code="MISSING_REF")
    ref = str(ref).strip()
    if done.is_done(ref):
        return "skipped"                                  # confirmed earlier for this business date
    if lookup(ref):                                       # an error here stops the item: never send blind
        if not dry_run:
            done.mark_done(ref)
            held.discard(ref)                             # a held send that arrived after all
        return "already there"
    if held.is_done(ref):
        raise unconfirmed(ref)                            # a send started earlier and never showed: a person checks
    if prepare is not None:                               # open and fill the form: safe to repeat
        attempt(prepare, name=f"prepare {ref}", log=log, **RETRY_PROFILES["ui"])
    if not dry_run:
        held.mark_done(ref)                               # before the send: a timeout, crash or Ctrl+C leaves it held
    sent, refused = [], []
    there = lambda: bool(sent) and lookup(ref)            # look again only once something may have been sent

    def go():
        if sent:                                          # try 2 only looks: the lookup said no, so never resend
            raise unconfirmed(ref)
        sent.append(ref)
        try:
            return act(send, dry_run=dry_run, log=lambda m: log(f"submit {ref}: {m}"))
        except BusinessError as e:
            if e.code != "NOT_JSON":                      # api_post's NOT_JSON comes after a 2xx: not a 'no'
                refused.append(ref)                       # the target said no: a definite 'not sent'
            raise

    try:
        attempt(go, verify=None if dry_run else there, already_done=there, name=f"submit {ref}", log=log,
                **dict(RETRY_PROFILES["submit"], tries=2))
    except BusinessError as e:
        if not refused:                                   # BAD_DATA or NOT_JSON from the reply, or a lookup error
            raise unconfirmed(ref) from e                 # after the send: it may have arrived, so it stays held
        held.discard(ref)                                 # refused: nothing to hold
        raise
    except SystemError_ as e:                             # not found in time, or the lookup could not tell
        raise unconfirmed(ref) from e
    if dry_run:
        return "dry run"
    done.mark_done(ref)                                   # at once: a crash after this line cannot resend
    held.discard(ref)
    return "submitted"


def rest_lookup(session, url, field="reference"):
    """lookup(ref) for a REST target: True only if a returned record carries ref exactly; 404 = not there."""
    def lookup(ref):
        try:
            rows = api_get(session, url, params={field: ref}) or []      # adapt if the API wraps its list
        except BusinessError as e:
            if e.context.get("status") != 404:
                raise                                     # 400 etc.: the reference itself is wrong
            return False
        return any(str(r.get(field, "")).strip() == str(ref).strip() for r in rows)   # not "the search found rows"
    return lookup


# --- demo ---


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0
    def clock(self): return self.now
    def sleep(self, s): self.now += s


class FakePortal:
    """Demo target. first: 'timeout' (item kept, reply lost), 'lost' (both), 'crash' (Ctrl+C mid-send), 'reject' (400),
    'garbled' / 'not_json' (item kept, reply unreadable), 'outage' (item kept, then the search fails), 'bug'."""
    def __init__(self, first="ok"): self.first, self.received, self.sends, self.down = first, [], 0, False

    def lookup(self, ref):
        if self.down: raise ConnectionError("search unavailable")
        return ref in self.received

    def send(self, ref):
        self.sends += 1
        mode = self.first if self.sends == 1 else "ok"
        if mode == "reject": raise BusinessError("HTTP 400: rejected", code="HTTP_400")
        if mode == "crash": raise KeyboardInterrupt
        if mode == "bug": raise TypeError("send() takes 0 positional arguments but 1 was given")
        if mode != "lost": self.received.append(ref)
        if mode == "garbled": raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if mode == "not_json": raise BusinessError("portal did not return JSON", code="NOT_JSON", status=200)
        self.down = mode == "outage"
        if mode in ("timeout", "lost", "outage"): raise TimeoutError("no reply within 60 s")
        return f"CONF-{ref}"


def demo(tmp):
    """Reply lost: one send, skipped on a re-run. Both lost, Ctrl+C mid-send, an unreadable reply or a failed search
    after the send: held, never resent, cleared once found. A refusal is released; a bug stops the run, held."""
    from contextlib import suppress
    from functools import partial
    from unittest import mock

    from toolbank import Checkpoint, process_all
    t, log = FakeTime(), []
    with mock.patch.dict(globals(), attempt=partial(attempt, sleep=t.sleep, clock=t.clock)):   # no real waits
        done, held = Checkpoint(tmp / "submitted.json"), Checkpoint(tmp / "unconfirmed.json")
        p = FakePortal(first="timeout")
        send = partial(p.send, "PAY-1")
        assert submit_once("PAY-1", send, p.lookup, done, held, dry_run=True, log=log.append) == "dry run"
        assert p.sends == 0 and not held.is_done("PAY-1") and log[-1].startswith("submit PAY-1: [dry-run] would call send(")
        assert submit_once(" PAY-1 ", send, p.lookup, done, held, log=log.append) == "submitted" and not held.is_done("PAY-1")
        assert submit_once("PAY-1", send, p.lookup, Checkpoint(tmp / "submitted.json"), held, log=log.append) == "skipped"

        def run(portal, ref):                             # one robot run over one item -> the failures table
            handler = lambda item: submit_once(item["ref"], partial(portal.send, item["ref"]), portal.lookup, done, held,
                                               log=log.append)
            return process_all([{"ref": ref}], handler, key=lambda item: item["ref"])[1]
        lost, killed = FakePortal(first="lost"), FakePortal(first="crash")
        with suppress(KeyboardInterrupt):                 # the robot is stopped mid-send
            run(killed, "PAY-3")
        for _ in range(2):                                # the run, then a re-run: never a second send
            assert run(lost, "PAY-2").loc[0, "code"] == run(killed, "PAY-3").loc[0, "code"] == "SUBMIT_UNCONFIRMED"
        assert p.sends == lost.sends == killed.sends == 1 and held.is_done("PAY-2") and not done.is_done("PAY-2")
        killed.received.append("PAY-3")                   # the portal shows it after all: the hold clears itself
        assert run(killed, "PAY-3").empty and done.is_done("PAY-3") and not held.is_done("PAY-3")
        for mode, ref in [("garbled", "PAY-5"), ("not_json", "PAY-6"), ("outage", "PAY-7")]:
            portal = FakePortal(first=mode)               # the reply or the search fails after the send: held
            assert run(portal, ref).loc[0, "code"] == "SUBMIT_UNCONFIRMED" and held.is_done(ref)
            portal.down = False                           # the next run finds it: done, never sent again
            assert run(portal, ref).empty and portal.sends == 1 and done.is_done(ref) and not held.is_done(ref)
        assert run(FakePortal(first="reject"), "PAY-4").loc[0, "code"] == "HTTP_400" and not held.is_done("PAY-4")
        assert run(FakePortal(), " ").loc[0, "code"] == "MISSING_REF"
        try:
            run(FakePortal(first="bug"), "PAY-8")         # a bug in send(): the run stops, the reference stays held
        except Exception as e:
            assert e.code == "ERROR" and isinstance(e.__cause__, TypeError) and held.is_done("PAY-8")
        else:
            raise AssertionError("a bug must stop the run")
