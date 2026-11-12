"""Business days, holidays, T+n settlement, ageing, cut-off times."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .robust import BusinessError


def load_holidays(path, col="date"):
    """Holiday dates from a CSV/Excel file with a 'date' column (maintain one per market). ISO / year-first text
    is read year-first, other text day-first; an unreadable date raises (a missed holiday = a wrong value date)."""
    from .clean import to_date
    p = Path(path)
    df = pd.read_csv(p, dtype=str) if p.suffix.lower() == ".csv" else pd.read_excel(p)
    d = to_date(df[col])
    bad = d.isna() & df[col].notna() & (df[col].astype("string").str.strip() != "").fillna(False)
    if bad.any():
        raise ValueError(f"unreadable holiday dates in {p.name}: {df.loc[bad, col].head(5).tolist()}")
    return set(d.dropna().dt.date)


def _d(x):
    """date from date / datetime / Timestamp / text ('2026-03-06', '06/03/2026' day-first); blank or unreadable
    -> BusinessError (never loop on NaT)."""
    if x is None or (not isinstance(x, (date, str)) and pd.isna(x)) or x is pd.NaT:
        raise BusinessError("blank date", code="BAD_DATE")
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):                       # same rules as clean.to_date: ISO / year-first, else day-first
        from .clean import to_date
        t = to_date(pd.Series([x])).iloc[0]
    else:
        try:
            t = pd.Timestamp(x)
        except (ValueError, TypeError):
            t = pd.NaT
    if pd.isna(t):
        raise BusinessError(f"unreadable date {x!r}", code="BAD_DATE")
    return t.date()


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
    d, hols, n = _d(d), _hols(holidays), int(n)
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


def convert_tz(series, from_tz, to_tz, naive_result=True, ambiguous="raise", nonexistent="shift_forward"):
    """Convert naive timestamps from one zone to another (e.g. NY trade times to SG): DST handled. The repeated
    hour when clocks go back raises unless ambiguous='NaT' (blank it) or a bool array (True = summer time)."""
    s = pd.to_datetime(series)
    if getattr(s.dt, "tz", None) is None:
        s = s.dt.tz_localize(from_tz, ambiguous=ambiguous, nonexistent=nonexistent)
    out = s.dt.tz_convert(to_tz)
    return out.dt.tz_localize(None) if naive_result else out


def month_end(d, business=False, holidays=()):
    """Last calendar (or business) day of d's month."""
    d, hols = _d(d), _hols(holidays)
    last = (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date()
    while business and not _bd(last, hols):
        last -= timedelta(days=1)
    return last


def assert_before_cutoff(cutoff="17:30", tz="Asia/Singapore", now=None, grace_minutes=0, business_date=None):
    """Minutes left before the cut-off (time of day in tz), else BusinessError PAST_CUTOFF: that item is handed
    over, never submitted late. business_date= also refuses a run on a later day (a Monday re-run for Friday's
    business date is past Friday's cut-off)."""
    now = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    hh, mm = map(int, cutoff.split(":"))
    if business_date is not None and now.date() > _d(business_date):
        raise BusinessError(f"past cut-off {cutoff} {tz} of {_d(business_date)}; today is {now:%Y-%m-%d}",
                            code="PAST_CUTOFF")
    limit = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(minutes=grace_minutes)
    if business_date is not None and now.date() < _d(business_date):
        return int((limit - now).total_seconds() // 60) + 24 * 60 * (_d(business_date) - now.date()).days
    if now > limit:
        margin = f" (stop at {limit:%H:%M}, {-grace_minutes} min before)" if grace_minutes < 0 else ""
        raise BusinessError(f"past cut-off {cutoff} {tz}{margin}; now {now:%H:%M}", code="PAST_CUTOFF")
    return int((limit - now).total_seconds() // 60)


def today_in(tz="Asia/Singapore"):
    """Today's date in a named time zone (not the server's)."""
    return datetime.now(ZoneInfo(tz)).date()
