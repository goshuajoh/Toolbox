"""
title: Tell people what happened, and hand over what needs a human
order: 120
when: >-
  The end of every run: the operations team sees that the robot ran and how it went, and the process owner gets
  the exceptions to work, as an email draft to review and send.
tags: [notify, teams, email, handover, exceptions, status]
needs: [requests]
blocks: [RobotError, build_email, df_to_html, get_secret, read_eml, save_eml, teams_message]
decisions:
  - "Status is one of three words: OK (nothing for a person), PARTIAL (some items need a person), FAILED (the run stopped, or nothing was done). Subject and card start with it, so an inbox rule or a glance finds it."
  - "Subject: '[PARTIAL] fx_breaks 2026-11-25: 48 done, 2 exceptions'. Robot name, business date (not the run date: a Monday re-run still reports Friday) and counts."
  - "The operations channel gets a Teams card on every run, OK included: a missing card is how people notice that the robot did not run. The card holds counts and the draft's file name only."
  - "The owner gets an email DRAFT (.eml with X-Unsent: 1; Outlook opens it with a Send button) only for PARTIAL or FAILED, and FAILED copies robot support. The robot never sends it."
  - "The draft body has the counts and the exceptions per code (df_to_html, at most 20 rows); the item list is only in the attached report."
  - "Notifications are not retried and never change the exit code: a failed card is logged, and the draft on disk is still the hand-over. A dry run writes the draft (it sends nothing) and only logs the card."
governance:
  - "Recipients come from config and must be in the bank's domain; any other address stops the hand-over with RobotError RECIPIENT, so a typo cannot send the report outside."
  - "A person reviews the draft and its attachment before pressing Send: four eyes on everything that leaves the robot with data in it."
  - "The webhook URL is a secret (anyone holding it can post to the channel): get_secret, never in code or config. A failed post logs the error type only, since a requests error message contains the URL."
  - "Teams cards and logs carry counts, codes and file names, never customer names, accounts or amounts."
  - "The draft stays in the run folder next to the report, under the data retention rule."
"""
import os
from pathlib import Path

import pandas as pd

from toolbank import RobotError, build_email, df_to_html, get_secret, read_eml, save_eml, teams_message

ROBOT, DOMAIN = "fx_breaks", "@bank.example"
SENDER = f"{ROBOT}.robot{DOMAIN}"                         # the robot's own mailbox, never a person's
OWNER = [f"fx.ops.lead{DOMAIN}"]                          # decides on exceptions (from config)
SUPPORT = [f"rpa.support{DOMAIN}"]                        # fixes the robot when the run FAILED (from config)
COLOUR = {"OK": "2EB886", "PARTIAL": "F2C744", "FAILED": "D13438"}


def run_status(done, exceptions, stopped=False):
    """OK, PARTIAL, or FAILED (the run stopped, or there are exceptions and nothing was done)."""
    if stopped or (exceptions and not done):
        return "FAILED"
    return "PARTIAL" if exceptions else "OK"


def subject_line(business_date, status, counts):
    """'[PARTIAL] fx_breaks 2026-11-25: 48 done, 2 exceptions'."""
    return f"[{status}] {ROBOT} {business_date}: {counts['done']} done, {counts['exceptions']} exceptions"


def internal_only(addresses):
    """Every recipient in the bank's domain, else RobotError RECIPIENT (a config error)."""
    outside = [a for a in addresses if not a.strip().lower().endswith(DOMAIN)]
    if outside:
        raise RobotError(f"{len(outside)} recipient(s) outside {DOMAIN}: fix the config", code="RECIPIENT")
    return list(addresses)


def handover_draft(folder, business_date, status, counts, failures, report):
    """Draft for the owner (support copied when FAILED): counts, exceptions per code, the report attached."""
    to, cc = internal_only(OWNER), internal_only(SUPPORT if status == "FAILED" else [])
    per_code = failures["code"].value_counts().rename_axis("code").reset_index(name="items")
    html = (f"<p>{ROBOT} ran for {business_date}: <b>{status}</b>. The items are in the attached report.</p>"
            + df_to_html(pd.DataFrame([counts])) + "<p>Exceptions per code:</p>" + df_to_html(per_code, max_rows=20))
    msg = build_email(SENDER, to, subject_line(business_date, status, counts), html=html, cc=cc, attachments=[report])
    return save_eml(msg, Path(folder) / f"{ROBOT}_{business_date}_handover.eml", draft=True)


def status_card(business_date, status, counts, draft=None, secret="TEAMS_WEBHOOK_OPS", dry_run=False, post=None):
    """Status-only Teams card for the operations channel: counts and the draft's file name, never data."""
    facts = {**counts, "hand-over": draft.name if draft else "none"}
    url = None if dry_run else get_secret(secret)
    return teams_message(url, subject_line(business_date, status, counts), facts, color=COLOUR[status],
                         dry_run=dry_run, post=post)


def hand_over(folder, business_date, counts, failures, report, *, stopped=False, secret="TEAMS_WEBHOOK_OPS",
              dry_run=False, post=None, log=print):
    """End of every run: the draft when a person must act, then the card. Returns (status, draft or None)."""
    status = run_status(counts["done"], counts["exceptions"], stopped)
    draft = handover_draft(folder, business_date, status, counts, failures, report) if status != "OK" else None
    try:
        status_card(business_date, status, counts, draft, secret=secret, dry_run=dry_run, post=post)
    except Exception as e:                                # not retried; the draft on disk is still the hand-over
        log(f"status card not posted: {type(e).__name__}")
    return status, draft


def demo(tmp):
    """PARTIAL: a draft with the report attached, read back; the card holds counts only. FAILED with a webhook that
    answers 500: logged, support copied, draft written. OK: card only. An outside address stops the hand-over."""
    failures = pd.DataFrame({"item": ["PAY-7", "PAY-9"], "code": ["PAST_CUTOFF", "BAD_IBAN"],
                             "message": ["past cut-off", "IBAN check digit wrong for Tan Ah Kow"]})
    report = tmp / f"{ROBOT}_report.xlsx"
    failures.to_excel(report, index=False)
    counts, log = {"items": 50, "done": 48, "exceptions": 2, "retry later": 0}, []

    status, draft = hand_over(tmp, "2026-11-25", counts, failures, report, dry_run=True, log=log.append)
    mail = read_eml(draft)
    assert status == "PARTIAL" and mail["subject"] == "[PARTIAL] fx_breaks 2026-11-25: 48 done, 2 exceptions"
    assert mail["to"] == OWNER[0] and not mail["cc"] and mail["attachments"] == [report.name] and not log
    assert "BAD_IBAN" in mail["html"] and "Tan Ah Kow" not in mail["html"] + mail["text"]
    assert b"X-Unsent: 1" in draft.read_bytes()
    card = status_card("2026-11-25", status, counts, draft, dry_run=True)
    assert [f["name"] for f in card["sections"][0]["facts"]] == [*counts, "hand-over"]
    assert "PAY-7" not in str(card) and "Tan Ah Kow" not in str(card)

    os.environ["DEMO_TEAMS_WEBHOOK"] = "https://hook.invalid/demo"     # get_secret reads env vars first
    posts, failed = [], dict(counts, done=0, exceptions=2, **{"retry later": 2})
    try:
        status, draft = hand_over(tmp, "2026-11-25", failed, failures, report, stopped=True,
                                  secret="DEMO_TEAMS_WEBHOOK", post=lambda url, json: posts.append(json) or 500,
                                  log=log.append)
    finally:
        os.environ.pop("DEMO_TEAMS_WEBHOOK", None)
    assert status == "FAILED" and len(posts) == 1 and log == ["status card not posted: RuntimeError"]
    assert read_eml(draft)["cc"] == SUPPORT[0] and draft.exists()

    ok = dict(counts, done=50, exceptions=0)
    assert hand_over(tmp, "2026-11-26", ok, failures.iloc[:0], report, dry_run=True) == ("OK", None)
    try:
        internal_only(OWNER + ["someone@gmail.com"])
    except RobotError as e:
        assert e.code == "RECIPIENT"
    else:
        raise AssertionError("expected RECIPIENT")
