"""Business days, holidays, T+n settlement, ageing, cut-off times."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .robust import RobotError


def load_holidays(path, col="date"):
    """Holiday dates from a CSV/Excel file with a 'date' column (maintain one per market)."""
    p = Path(path)
    df = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_excel(p)
    return set(pd.to_datetime(df[col], format="mixed", dayfirst=True).dt.date)


def _d(x):
    """date from date / datetime / Timestamp / 'YYYY-MM-DD' text."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return pd.Timestamp(x).date()


def _hols(holidays):
    """Holidays as a set of dates, whatever they came as (dates, Timestamps or text)."""
    return {_d(h) for h in holidays} if holidays is not None and len(holidays) else set()


def _bd(d, hols):
    return d.weekday() < 5 and d not in hols


def is_business_day(d, holidays=()):
    """Mon-Fri and not a holiday (dates, Timestamps or 'YYYY-MM-DD' text all work)."""
    return _bd(_d(d), _hols(holidays))


def add_business_days(d, n, holidays=()):
    """T+n (or T-n) skipping weekends and holidays."""
    d, hols = _d(d), _hols(holidays)
    step = 1 if n >= 0 else -1
    while n:
        d += timedelta(days=step)
        if _bd(d, hols):
            n -= step
    return d


def next_business_day(d, holidays=(), inclusive=False):
    """Same day if inclusive and it is a business day, else the next one."""
    d, hols = _d(d), _hols(holidays)
    if not (inclusive and _bd(d, hols)):
        d += timedelta(days=1)
        while not _bd(d, hols):
            d += timedelta(days=1)
    return d


def business_days_between(a, b, holidays=()):
    """Business days in (a, b] — ageing. Negative if b < a."""
    a, b, hols = _d(a), _d(b), _hols(holidays)
    if b < a:
        return -business_days_between(b, a, hols)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += _bd(d, hols)
    return n


def adjust_business_day(d, holidays=(), convention="modified_following"):
    """Roll a non-business day: 'following', 'preceding', 'modified_following' (stay in month), 'modified_preceding'."""
    d, hols = _d(d), _hols(holidays)
    if convention in (None, "none", "unadjusted") or _bd(d, hols):
        return d
    if convention not in ("following", "preceding", "modified_following", "modified_preceding"):
        raise ValueError(f"unknown convention {convention!r}")
    step = 1 if convention in ("following", "modified_following") else -1
    x = d
    while not _bd(x, hols):
        x += timedelta(days=step)
    if convention.startswith("modified") and x.month != d.month:
        x = d
        while not _bd(x, hols):
            x -= timedelta(days=step)
    return x


def convert_tz(series, from_tz, to_tz, naive_result=True):
    """Convert naive timestamps from one zone to another (e.g. NY trade times to SG): DST handled."""
    s = pd.to_datetime(series)
    if getattr(s.dt, "tz", None) is None:
        s = s.dt.tz_localize(from_tz, ambiguous="NaT", nonexistent="shift_forward")
    out = s.dt.tz_convert(to_tz)
    return out.dt.tz_localize(None) if naive_result else out


def month_end(d, business=False, holidays=()):
    """Last calendar (or business) day of d's month."""
    d, hols = _d(d), _hols(holidays)
    last = (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date()
    while business and not _bd(last, hols):
        last -= timedelta(days=1)
    return last


def assert_before_cutoff(cutoff="17:30", tz="Asia/Singapore", now=None, grace_minutes=0):
    """Raise RobotError(PAST_CUTOFF) if local time in tz is past the deadline."""
    now = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    hh, mm = map(int, cutoff.split(":"))
    limit = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(minutes=grace_minutes)
    if now > limit:
        raise RobotError(f"past cut-off {cutoff} {tz}; now {now:%H:%M}", code="PAST_CUTOFF")
    return int((limit - now).total_seconds() // 60)


def today_in(tz="Asia/Singapore"):
    """Today's date in a named time zone (not the server's)."""
    return datetime.now(ZoneInfo(tz)).date()
