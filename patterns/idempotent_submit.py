"""
title: Submit, pay or send exactly once
order: 30
when: "Any step whose repeat would do harm: posting a payment, submitting an instruction or a case, sending a file to another team. One call per item, keyed by its business reference."
tags: [submit, retry, items]
needs: [requests]
blocks: [RETRY_PROFILES, BusinessError, Checkpoint, SystemError_, act, api_get, attempt, process_all]
decisions:
  - "Key every item by its business reference (payment id, instruction ref), trimmed, never a row number: rows move when a file is re-sorted, a reference does not. A blank one is BusinessError MISSING_REF. Key process_all(checkpoint=...) by the same trimmed reference, so one repeated in the input is reported (DUPLICATE_KEY), not returned as 'skipped'."
  - "Look the reference up before the send, outside attempt (attempt reads an error in already_done as 'not there'). lookup(ref) raises when it cannot search and is True only for an exact match: rest_lookup compares trimmed text, case counts (a wrong 'found' would drop a payment silently, a wrong 'not found' only holds it), 404 is not there, other 4xx is BusinessError."
  - "prepare() (open the form, fill it) runs first as its own screen step (RETRY_PROFILES['ui'], up to 3 tries). send() (press Submit, POST) is the point of no return: RETRY_PROFILES['submit'] with tries raised to 2 only because already_done is given, and the second try never sends, it looks the reference up 2 s after a failed send."
  - "Confirmed means the lookup finds the reference within 30 s, polled every 5 s (it is a search, not a screen check), not that send() returned. The checkpoint (its own file, submitted.json) is marked right after, so later steps of the item can fail and re-run without a resend."
  - "The reference goes on the hold list (unconfirmed.json) just before send() and comes off when the lookup finds it. Anything else after that point (a timeout, a slow back-end, a crash, Ctrl+C, a 401) leaves it held, never checkpointed: SystemError_ SUBMIT_UNCONFIRMED (at once, or at the next run after a crash), and every later run raises it again without sending."
  - "A refusal (BusinessError from send(), e.g. HTTP 400) is a definite 'not sent': off the hold list, recorded as a business exception, not retried in this run. Raise BusinessError in send() only when the target said no."
  - "process_all stops after 3 system failures in a row, so an outage leaves at most 3 references on hold, not the whole batch. Until they are found or cleared, a re-run that meets them in a row stops there again."
governance:
  - "The robot takes a reference off unconfirmed.json only when the lookup finds it or the target refuses it. Any other removal is a person's, after checking the target system: that edit is the approval to send it again."
  - "Keep unconfirmed.json outside the dated run folder (e.g. work/<robot>/unconfirmed.json) so a hold survives into the next business date. The checkpoint covers one business date; the lookup in the target system is the guard across days and machines."
  - "For an API use http_session() (it never resends a POST) and send the reference as an idempotency key if the API accepts one."
  - "This block's own errors carry the reference only. attempt's log lines and a refusal carry the target's error text (an HTTP 400 or 503 body), which can echo names or amounts that get_logger does not mask: in send(), catch it and raise your own error with a short code."
  - "Rehearse with dry_run=True before the first live run: it runs the lookup and prepare(), logs each send through act instead of making it and writes neither file; the returned status shows what is already there. If the target saves drafts, skip prepare() in a dry run."
"""
import time
from contextlib import suppress
from functools import partial

import pandas as pd

from toolbank import RETRY_PROFILES, BusinessError, Checkpoint, SystemError_, act, api_get, attempt, process_all


def unconfirmed(ref):
    """The error for a send that may have arrived but does not show in the target system."""
    return SystemError_("sent but not confirmed: check the target system before it is sent again",
                        code="SUBMIT_UNCONFIRMED", ref=ref)


def release(held, ref):
    """Take ref off the hold list."""
    held.discard(ref)


def submit_once(ref, send, lookup, done, held, *, prepare=None, dry_run=False, log=print,
                sleep=time.sleep, clock=time.monotonic):
    """Send one item once (done, held: Checkpoint); returns 'skipped', 'already there', 'dry run' or 'submitted'."""
    if pd.isna(ref) or not str(ref).strip():
        raise BusinessError("no business reference: cannot check whether it was sent", code="MISSING_REF")
    ref = str(ref).strip()
    if done.is_done(ref):
        return "skipped"                                  # confirmed earlier for this business date
    if lookup(ref):                                       # an error here stops the item: never send blind
        if not dry_run:
            done.mark_done(ref)
            release(held, ref)                            # a held send that arrived after all
        return "already there"
    if held.is_done(ref):
        raise unconfirmed(ref)                            # a send started earlier and never showed: a person checks
    if prepare is not None:                               # open and fill the form: safe to repeat
        attempt(prepare, name=f"prepare {ref}", log=log, sleep=sleep, clock=clock, **RETRY_PROFILES["ui"])
    if not dry_run:
        held.mark_done(ref)                               # before the send: a timeout, crash or Ctrl+C leaves it held
    sent = []
    there = lambda: bool(sent) and lookup(ref)            # look again only once something may have been sent

    def go():
        if sent:                                          # try 2 only looks: the lookup said no, so never resend
            raise unconfirmed(ref)
        sent.append(ref)
        return act(send, dry_run=dry_run, log=lambda m: log(f"submit {ref}: {m}"))

    try:
        attempt(go, verify=None if dry_run else there, already_done=there, name=f"submit {ref}", log=log,
                poll_s=5.0, sleep=sleep, clock=clock, **dict(RETRY_PROFILES["submit"], tries=2))
    except BusinessError:
        release(held, ref)                                # the target said no: nothing to hold
        raise
    except SystemError_ as e:
        raise unconfirmed(ref) from e
    if dry_run:
        return "dry run"
    done.mark_done(ref)                                   # at once: a crash after this line cannot resend
    release(held, ref)
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


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0
    def clock(self): return self.now
    def sleep(self, s): self.now += s


class FakePortal:
    """Demo target. first: 'timeout' (item kept, reply lost), 'lost' (both), 'crash' (Ctrl+C mid-send), 'reject' (400)."""
    def __init__(self, first="ok"): self.first, self.received, self.sends = first, [], 0
    def lookup(self, ref): return ref in self.received

    def send(self, ref):
        self.sends += 1
        mode = self.first if self.sends == 1 else "ok"
        if mode == "reject": raise BusinessError("HTTP 400: rejected", code="HTTP_400")
        if mode == "crash": raise KeyboardInterrupt
        if mode != "lost": self.received.append(ref)
        if mode in ("timeout", "lost"): raise TimeoutError("no reply within 60 s")
        return f"CONF-{ref}"


def demo(tmp):
    """Reply lost: one send, skipped on a re-run. Both lost or Ctrl+C mid-send: held, never resent, cleared once found."""
    t, log = FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    done, held = Checkpoint(tmp / "submitted.json"), Checkpoint(tmp / "unconfirmed.json")
    p = FakePortal(first="timeout")
    send = partial(p.send, "PAY-1")
    assert submit_once("PAY-1", send, p.lookup, done, held, dry_run=True, **kw) == "dry run"
    assert p.sends == 0 and not held.is_done("PAY-1") and log[-1].startswith("submit PAY-1: [dry-run] would call send(")
    assert submit_once(" PAY-1 ", send, p.lookup, done, held, **kw) == "submitted" and not held.is_done("PAY-1")
    assert submit_once("PAY-1", send, p.lookup, Checkpoint(tmp / "submitted.json"), held, **kw) == "skipped"

    def run(portal, ref):                                 # one robot run over one item -> the failures table
        handler = lambda item: submit_once(item["ref"], partial(portal.send, item["ref"]), portal.lookup, done, held, **kw)
        return process_all([{"ref": ref}], handler, key=lambda item: item["ref"])[1]
    lost, killed = FakePortal(first="lost"), FakePortal(first="crash")
    with suppress(KeyboardInterrupt):                     # the robot is stopped mid-send
        run(killed, "PAY-3")
    for _ in range(2):                                    # the run, then a re-run: never a second send
        assert run(lost, "PAY-2").loc[0, "code"] == run(killed, "PAY-3").loc[0, "code"] == "SUBMIT_UNCONFIRMED"
    assert p.sends == lost.sends == killed.sends == 1 and held.is_done("PAY-2") and not done.is_done("PAY-2")
    killed.received.append("PAY-3")                       # the portal shows it after all: the hold clears itself
    assert run(killed, "PAY-3").empty and done.is_done("PAY-3") and not held.is_done("PAY-3")
    assert run(FakePortal(first="reject"), "PAY-4").loc[0, "code"] == "HTTP_400" and not held.is_done("PAY-4")
    assert run(FakePortal(), " ").loc[0, "code"] == "MISSING_REF"
