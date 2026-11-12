"""
title: Run only on business days and before the cut-off
order: 110
when: >-
  A robot that is scheduled every day but must only work on business days, or that submits, pays or sends
  something with a deadline and a value date.
tags: [calendar, holidays, cut-off, value-date, time-zone, scheduling]
needs: []
blocks: [BusinessError, RobotError, add_business_days, adjust_business_day, assert_before_cutoff, is_business_day, load_holidays, month_end, process_all, today_in]
decisions:
  - "The business date is today_in(team time zone), read once at the start and passed to every step. A server in another zone, or a run that crosses midnight, does not change it."
  - "Weekend or team holiday: log 'nothing to do' and exit 0. The scheduler may fire every day; the calendar decides, not the schedule."
  - "One holiday file per market (holidays_SG.csv, holidays_US.csv). Missing, unreadable, or with no dates for this year and the year 14 days ahead: RobotError HOLIDAYS (exit 5). A stale file turns every holiday into a working day, so the run stops."
  - "T+n value dates count business days on the joint calendar of the team and the settlement markets: a US holiday moves a USD value date even when Singapore works."
  - "A scheduled date on a weekend or holiday rolls modified following: forward, but back when forward leaves the month, so a month-end payment stays in its month."
  - "The cut-off check runs right before each submit, not once at the start: a long batch can cross it. Submits stop 15 minutes before the cut-off, so the last one still lands in time on a slow day."
  - "Past the cut-off: BusinessError PAST_CUTOFF for that item. process_all lists every remaining item on the exceptions report (exit 2) and nothing is submitted late. Not SystemError_: a re-run would only be later."
governance:
  - "Holiday files are reference data under change control: one owner per market, next year's dates loaded by 1 December, each file's SHA-256 in the run evidence."
  - "There is no switch to submit after the cut-off. A late submission is a person's decision, made and done outside the robot."
  - "Each cut-off is written with its own time zone (USD 17:00 America/New_York), never converted by hand: daylight saving moves the gap twice a year."
  - "Logs carry the business date, the minutes left and item ids, never amounts or accounts."
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toolbank import (BusinessError, RobotError, add_business_days, adjust_business_day, assert_before_cutoff,
                      is_business_day, load_holidays, month_end, process_all, today_in)

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
    """The dates for this run, or None on a weekend or team holiday (log 'nothing to do', exit 0)."""
    if isinstance(today, str):
        today = date.fromisoformat(today)                 # a re-run: --business-date 2026-11-25
    today = today or today_in(TEAM_TZ)                    # the team's date, not the server's
    team = market_holidays(folder, [TEAM_MARKET], today)
    if not is_business_day(today, team):
        return None
    settle = team | market_holidays(folder, settle_markets, today)
    return {"business_date": today, "holidays": settle,
            "value_date": add_business_days(today, SPOT_DAYS, settle),
            "month_end": month_end(today, business=True, holidays=team)}


def roll(scheduled, holidays):
    """A scheduled date on a weekend or holiday -> modified following (never into the next month)."""
    return adjust_business_day(scheduled, holidays, "modified_following")


def before_cutoff(cutoff="17:30", tz=TEAM_TZ, margin_min=MARGIN_MIN, now=None):
    """Call right before each submit: minutes left, or BusinessError PAST_CUTOFF (hand over, never submit late)."""
    last = (datetime.strptime(cutoff, "%H:%M") - timedelta(minutes=margin_min)).strftime("%H:%M")
    try:
        return assert_before_cutoff(last, tz, now=now)
    except RobotError as e:
        raise BusinessError(f"{e.message}: hand over to a person", code="PAST_CUTOFF") from e


def demo(tmp):
    """2026 calendar: Christmas is skipped, T+2 steps over US Thanksgiving, a Sunday 31st stays in May, submits
    at 17:20 Singapore time are handed over, and a stale, unreadable or missing holiday file stops the run."""
    (tmp / "holidays_SG.csv").write_text("date\n2026-01-01\n2026-12-25\n2027-01-01\n")
    (tmp / "holidays_US.csv").write_text("date\n2026-01-01\n2026-11-26\n2026-12-25\n2027-01-01\n")
    assert plan_run(tmp, ["US"], today=date(2026, 12, 25)) is None             # holiday: exit 0, nothing to do
    p = plan_run(tmp, ["US"], today="2026-11-25")                             # Wednesday
    assert p["value_date"] == date(2026, 11, 30)                              # Thu 26 is a US holiday: Fri, Mon
    assert add_business_days(p["business_date"], 2, {date(2026, 1, 1)}) == date(2026, 11, 27)   # SG alone: Fri
    assert p["month_end"] == date(2026, 11, 30)
    assert roll(date(2026, 5, 31), p["holidays"]) == date(2026, 5, 29)        # not Monday 1 June

    assert before_cutoff(now=datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo(TEAM_TZ))) == 435   # minutes to 17:15
    late, sent = datetime(2026, 11, 25, 9, 20, tzinfo=timezone.utc), []      # 17:20 in Singapore

    def submit(ref):
        before_cutoff(now=late)
        sent.append(ref)
    _, failures = process_all(["PAY-1", "PAY-2"], submit)
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
