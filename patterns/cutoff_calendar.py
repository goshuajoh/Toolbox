"""
title: Run only on business days and before the cut-off
order: 110
when: >-
  A robot that is scheduled every day but must only work on business days, or that submits, pays or sends
  something with a deadline and a value date.
tags: [calendar, submit]
needs: []
blocks: [RobotError, add_business_days, adjust_business_day, assert_before_cutoff, file_sha256, is_business_day, load_holidays, month_end, process_all, today_in]
decisions:
  - "The business date is today_in(team time zone), read once at the start and passed to every step. A server in another zone, or a run that crosses midnight, does not change it."
  - "A re-run may name an earlier business date (--business-date), never a later one: RobotError BUSINESS_DATE (exit 5). The cut-off check alone would count down to that later day's cut-off, so a typo would submit days early."
  - "Weekend or team holiday: log 'nothing to do' and exit 0. The scheduler may fire every day; the calendar decides, not the schedule."
  - "One holiday file per market (holidays_SG.csv, holidays_US.csv). Missing, unreadable, or with no dates for this year and the year 14 days ahead: RobotError HOLIDAYS (exit 5). A stale file turns every holiday into a working day, so the run stops."
  - "T+n value dates count business days on the joint calendar of the team and the settlement markets: a US holiday moves a USD value date even when Singapore works."
  - "A scheduled date on a weekend or holiday rolls modified following: forward, but back when forward leaves the month, so a month-end payment stays in its month."
  - "The cut-off check runs right before each submit, not once at the start: a long batch can cross it. Submits stop 15 minutes before the cut-off (grace_minutes=-15), so the last one still lands in time on a slow day."
  - "The check passes business_date=, so it also compares today's date in the cut-off's zone with the business date: a batch that runs past midnight, or a re-run on Monday for Friday, is past Friday's cut-off. Without business_date= only the time of day is compared."
  - "Past the cut-off: BusinessError PAST_CUTOFF for that item. process_all lists every remaining item on the exceptions report (exit 2) and nothing is submitted late. Not SystemError_: a re-run would only be later."
governance:
  - "Holiday files are reference data under change control: one owner per market, next year's dates loaded by 1 December. The plan holds each file's SHA-256 (holiday_files) for the run evidence."
  - "There is no switch to submit after the cut-off. A late submission is a person's decision, made and done outside the robot."
  - "Each cut-off is set as a local time with its zone name (17:30 Asia/Singapore), never as a time converted by hand to the server's zone: daylight saving moves such a conversion twice a year."
  - "PAST_CUTOFF and HOLIDAYS messages carry dates, times, the zone and the market; process_all adds the item id. Never amounts or accounts."
"""
from datetime import date, timedelta
from pathlib import Path

from toolbank import (RobotError, add_business_days, adjust_business_day, assert_before_cutoff, file_sha256,
                      is_business_day, load_holidays, month_end, today_in)

TEAM_TZ, TEAM_MARKET = "Asia/Singapore", "SG"             # where the operations team works
SPOT_DAYS, MARGIN_MIN = 2, 15                             # T+2 value dates; stop submitting 15 min early


def market_holidays(folder, markets, today, horizon_days=14):
    """Holidays of all the markets in one set; a missing, unreadable or out-of-date file stops the run."""
    years, hols = {today.year, (today + timedelta(days=horizon_days)).year}, set()
    for m in markets:
        try:
            days = load_holidays(Path(folder) / f"holidays_{m}.csv")
        except (OSError, ValueError, KeyError) as e:
            raise RobotError(f"holiday file for {m} unreadable: {e}", code="HOLIDAYS", market=m) from e
        if not years <= {d.year for d in days}:
            raise RobotError(f"holiday file for {m} has no dates for {sorted(years)}", code="HOLIDAYS", market=m)
        hols |= days
    return hols


def plan_run(folder, settle_markets, today=None):
    """The dates for this run, or None on a weekend or team holiday (log 'nothing to do', exit 0). A business date
    after today in the team's zone stops the run (RobotError BUSINESS_DATE)."""
    if isinstance(today, str):
        today = date.fromisoformat(today)                 # a re-run: --business-date 2026-11-25
    now = today_in(TEAM_TZ)                               # the team's date, not the server's
    today = today or now
    if today > now:                                       # a typo must not submit days early
        raise RobotError(f"business date {today} is after today {now} in {TEAM_TZ}", code="BUSINESS_DATE")
    team = market_holidays(folder, [TEAM_MARKET], today)
    if not is_business_day(today, team):
        return None
    settle = team | market_holidays(folder, settle_markets, today)
    files = [Path(folder) / f"holidays_{m}.csv" for m in dict.fromkeys([TEAM_MARKET, *settle_markets])]
    return {"business_date": today, "holidays": settle,
            "value_date": add_business_days(today, SPOT_DAYS, settle),
            "month_end": month_end(today, business=True, holidays=team),
            "holiday_files": {p.name: file_sha256(p) for p in files}}


def roll(scheduled, holidays):
    """A scheduled date on a weekend or holiday -> modified following (never into the next month)."""
    return adjust_business_day(scheduled, holidays, "modified_following")


def before_cutoff(business_date, cutoff="17:30", tz=TEAM_TZ, margin_min=MARGIN_MIN):
    """Call right before each submit: minutes left, or BusinessError PAST_CUTOFF (hand over, never submit late).
    Past midnight in tz, or a re-run on a later day than business_date, is past the cut-off too."""
    return assert_before_cutoff(cutoff, tz, grace_minutes=-margin_min, business_date=business_date)


# --- demo ---


def demo(tmp):
    """2026 calendar: Christmas is skipped, T+2 steps over US Thanksgiving, a Sunday 31st stays in May, submits
    at 17:20 Singapore time (09:20 on a UTC server) or on a later day are handed over, and a business date in the
    future or a stale, unreadable or missing holiday file stops the run."""
    from datetime import datetime, timezone
    from functools import partial
    from unittest import mock
    from zoneinfo import ZoneInfo

    from toolbank import process_all

    def at(now):                                          # the demo's clock: what assert_before_cutoff takes as now
        return mock.patch.dict(globals(), assert_before_cutoff=partial(assert_before_cutoff, now=now))

    def on(day):                                          # the demo's calendar: what today_in returns
        return mock.patch.dict(globals(), today_in=lambda tz: day)

    (tmp / "holidays_SG.csv").write_text("date\n2026-01-01\n2026-12-25\n2027-01-01\n")
    (tmp / "holidays_US.csv").write_text("date\n2026-01-01\n2026-11-26\n2026-12-25\n2027-01-01\n")
    with on(date(2026, 12, 25)):
        assert plan_run(tmp, ["US"]) is None                                   # holiday: exit 0, nothing to do
    with on(date(2026, 11, 25)):                                               # Wednesday
        p = plan_run(tmp, ["US"])
        try:
            plan_run(tmp, ["US"], today="2026-11-27")                          # a typo: Friday
        except RobotError as e:
            assert e.code == "BUSINESS_DATE"
        else:
            raise AssertionError("a later business date must stop the run")
    assert p["value_date"] == date(2026, 11, 30)                              # Thu 26 is a US holiday: Fri, Mon
    assert add_business_days(p["business_date"], 2, {date(2026, 1, 1)}) == date(2026, 11, 27)   # SG alone: Fri
    assert p["month_end"] == date(2026, 11, 30)
    assert roll(date(2026, 5, 31), p["holidays"]) == date(2026, 5, 29)        # not Monday 1 June
    assert sorted(p["holiday_files"]) == ["holidays_SG.csv", "holidays_US.csv"]
    assert p["holiday_files"]["holidays_US.csv"] == file_sha256(tmp / "holidays_US.csv")

    day, sent = p["business_date"], []
    with at(datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo(TEAM_TZ))):
        assert before_cutoff(day) == 435                                      # minutes to 17:15

    def submit(ref):
        before_cutoff(day)
        sent.append(ref)
    for now in (datetime(2026, 11, 25, 9, 20, tzinfo=timezone.utc),          # 17:20 in Singapore
                datetime(2026, 11, 26, 9, 0, tzinfo=ZoneInfo(TEAM_TZ))):     # a re-run for the 25th, next morning
        with at(now):
            _, failures = process_all(["PAY-1", "PAY-2"], submit)            # BusinessError: listed, not raised
        assert sent == [] and failures["code"].tolist() == ["PAST_CUTOFF"] * 2 and not failures["retryable"].any()

    (tmp / "holidays_US.csv").write_text("date\n2025-01-01\n2025-12-25\n")     # not updated for 2026
    (tmp / "holidays_GB.csv").write_text("date\nBoxing Day\n")                 # unreadable; HK has no file
    for market in ("US", "GB", "HK"):
        try:
            market_holidays(tmp, [market], date(2026, 11, 25))
        except RobotError as e:
            assert e.code == "HOLIDAYS" and not e.retryable
        else:
            raise AssertionError(f"expected HOLIDAYS for {market}")
