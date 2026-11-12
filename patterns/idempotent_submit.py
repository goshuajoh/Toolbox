"""
title: Submit, pay or send exactly once
order: 30
when: >-
  Any step whose repeat would do harm: posting a payment, submitting an instruction or a case, sending a file to
  another team. One call per item, keyed by its business reference.
tags: [submit, payment, idempotent, checkpoint, dry-run, retry]
needs: [requests]
blocks: [RETRY_PROFILES, BusinessError, Checkpoint, SystemError_, act, api_get, attempt, process_all]
decisions:
  - "Key every item by its business reference (payment id, instruction ref), never a row number: rows move when a file is re-sorted, a reference does not."
  - "Look the reference up before the send, outside attempt (attempt reads an error in already_done as 'not there'). lookup(ref) raises when it cannot search and is True only for an exact match: rest_lookup treats 404 as not there, other 4xx as BusinessError, and a search that ignores its filter finds nothing."
  - "RETRY_PROFILES['submit'] with tries raised to 2 only because already_done is given: 2 s after a failed send it looks the reference up again. A slow back-end leads to a hold, never a resend."
  - "prepare() (open the form, fill it) may run twice; send() (press Submit, POST) is the point of no return. Once send() has started, a failure leads to one more lookup, never a second send."
  - "Confirmed means the lookup finds the reference within 30 s, polled every 5 s (it is a search, not a screen check), not that send() returned."
  - "Ambiguous (the reply timed out and the lookup finds nothing): SystemError_ SUBMIT_UNCONFIRMED, no checkpoint, the reference goes on the hold list; later runs raise again without sending."
  - "A rejection (BusinessError, e.g. HTTP 400) is a definite answer: recorded as a business exception, never held, never retried."
  - "Mark the checkpoint (its own file, submitted.json) right after the confirmation, so later steps of the item can fail and re-run without a resend."
  - "process_all stops after 3 system failures in a row, so an outage leaves at most 3 references on hold, not the whole batch."
  - "dry_run=True runs the lookup and prepare(), logs the send through act and writes no checkpoint. If the target saves drafts, skip prepare() in a dry run."
governance:
  - "Only a person removes a reference from unconfirmed.json, after checking the target system: that edit is the approval to send it again."
  - "Keep unconfirmed.json outside the dated run folder (e.g. work/<robot>/unconfirmed.json) so a hold survives into the next business date."
  - "The checkpoint covers one business date; the lookup in the target system is the guard across days and machines."
  - "For an API use http_session() (it never resends a POST) and send the reference as an idempotency key if the API accepts one."
  - "Logs and errors carry the reference only, never amounts, names or account numbers."
  - "Rehearse with dry_run=True before the first live run: the log shows what would be sent and what is already there."
"""
import time
from functools import partial

from toolbank import RETRY_PROFILES, BusinessError, Checkpoint, SystemError_, act, api_get, attempt, process_all


def unconfirmed(ref):
    """The error for a send that may have arrived but does not show in the target system."""
    return SystemError_("sent but not confirmed: check the target system before it is sent again",
                        code="SUBMIT_UNCONFIRMED", ref=ref)


def submit_once(ref, send, lookup, done, held, *, prepare=None, dry_run=False, log=print,
                sleep=time.sleep, clock=time.monotonic):
    """Send one item once (done, held: Checkpoint); returns 'skipped', 'already there', 'dry run' or 'submitted'."""
    if done.is_done(ref):
        return "skipped"                                  # confirmed earlier for this business date
    if lookup(ref):                                       # an error here stops the item: never send blind
        if not dry_run:
            done.mark_done(ref)
        return "already there"
    if held.is_done(ref):
        raise unconfirmed(ref)                            # sent once, never confirmed: a person checks first
    sent = []

    def there():
        return bool(sent) and lookup(ref)                 # look again only once something may have been sent

    def go():
        if sent:                                          # the last send may have arrived though the lookup says no
            raise unconfirmed(ref)
        if prepare is not None:
            prepare()                                     # open and fill the form: safe to repeat
        sent.append(ref)                                  # from here on a failure means "maybe sent"
        return act(send, dry_run=dry_run, log=lambda m: log(f"submit {ref}: {m}"))

    try:
        attempt(go, verify=None if dry_run else there, already_done=there, name=f"submit {ref}", log=log,
                poll_s=5.0, sleep=sleep, clock=clock, **dict(RETRY_PROFILES["submit"], tries=2))
    except SystemError_ as e:
        if not sent:
            raise                                         # failed before the point of no return
        held.mark_done(ref)
        raise unconfirmed(ref) from e
    if dry_run:
        return "dry run"
    done.mark_done(ref)                                   # at once: a crash after this line cannot resend
    return "submitted" if sent else "already there"


def rest_lookup(session, url, field="reference"):
    """lookup(ref) for a REST target: True only if a returned record carries ref exactly; 404 = not there."""
    def lookup(ref):
        try:
            rows = api_get(session, url, params={field: ref}) or []      # adapt if the API wraps its list
        except BusinessError as e:
            if e.context.get("status") != 404:
                raise                                     # 400 etc.: the reference itself is wrong
            return False
        return any(str(r.get(field)) == ref for r in rows)   # never "the search returned something"
    return lookup


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakePortal:
    """Target system for the demo. first='timeout': keeps the item, loses the reply; 'lost': loses both."""
    def __init__(self, first="ok"):
        self.first, self.received, self.sends = first, [], 0

    def lookup(self, ref):
        return ref in self.received

    def send(self, ref):
        self.sends += 1
        if not (self.sends == 1 and self.first == "lost"):
            self.received.append(ref)
        if self.sends == 1 and self.first != "ok":
            raise TimeoutError("no reply within 60 s")
        return f"CONF-{ref}"


def demo(tmp):
    """Reply lost after the portal stored it: one send, confirmed by the lookup, skipped on a re-run.
    Both lost: held for a person and never sent again. A dry run sends nothing; a failed fill is repeated."""
    t, log = FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    done, held = Checkpoint(tmp / "submitted.json"), Checkpoint(tmp / "unconfirmed.json")

    p = FakePortal(first="timeout")
    send = partial(p.send, "PAY-1")
    assert submit_once("PAY-1", send, p.lookup, done, held, dry_run=True, **kw) == "dry run"
    assert p.sends == 0 and not done.is_done("PAY-1") and log[-1].startswith("submit PAY-1: [dry-run] would call send(")
    assert submit_once("PAY-1", send, p.lookup, done, held, **kw) == "submitted"
    assert p.received == ["PAY-1"] and p.sends == 1 and done.is_done("PAY-1")
    rerun = Checkpoint(tmp / "submitted.json")
    assert submit_once("PAY-1", send, p.lookup, rerun, held, **kw) == "skipped" and p.sends == 1

    lost = FakePortal(first="lost")
    handler = lambda item: submit_once(item["ref"], partial(lost.send, item["ref"]), lost.lookup, done, held, **kw)
    for _ in range(2):                                    # the run, then a re-run: never a second send
        _, failures = process_all([{"ref": "PAY-2"}], handler, key=lambda item: item["ref"])
        assert failures.loc[0, "code"] == "SUBMIT_UNCONFIRMED" and failures.loc[0, "retryable"]
    assert lost.sends == 1 and held.is_done("PAY-2") and not done.is_done("PAY-2")

    ok, fills = FakePortal(), []
    def prepare():
        fills.append(1)
        if len(fills) == 1:
            raise OSError("form not ready")
    assert submit_once("PAY-3", partial(ok.send, "PAY-3"), ok.lookup, done, held, prepare=prepare, **kw) == "submitted"
    assert len(fills) == 2 and ok.sends == 1
