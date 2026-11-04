"""Excel formulas with Excel's behaviour, for columns: ROUND (half away from zero), ROUNDUP/DOWN, IFERROR,
COALESCE, TEXTJOIN, EDATE, EOMONTH, NETWORKDAYS, WORKDAY, DATEDIF, TEXT(date, "dd/mm/yyyy"), RANK, SUMPRODUCT."""
import decimal

import numpy as np
import pandas as pd


def _decimal_round(x, ndigits, mode):
    if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA:
        return np.nan
    q = decimal.Decimal(1).scaleb(-ndigits)
    return float(decimal.Decimal(str(x)).quantize(q, rounding=mode))


def _apply(x, f):
    if isinstance(x, pd.Series):
        return x.map(f).astype("float64")
    return f(x)


def round_half_up(x, ndigits=0):
    """Excel ROUND: 2.5 -> 3, 0.125 -> 0.13, -2.5 -> -3. (Python's round() and pandas .round() give 2 and 0.12.)"""
    return _apply(x, lambda v: _decimal_round(v, ndigits, decimal.ROUND_HALF_UP))


def roundup(x, ndigits=0):
    """Excel ROUNDUP: away from zero."""
    return _apply(x, lambda v: _decimal_round(v, ndigits, decimal.ROUND_UP))


def rounddown(x, ndigits=0):
    """Excel ROUNDDOWN: towards zero (truncate)."""
    return _apply(x, lambda v: _decimal_round(v, ndigits, decimal.ROUND_DOWN))


def ceiling(x, ndigits=0):
    """Round towards +infinity at ndigits."""
    return _apply(x, lambda v: _decimal_round(v, ndigits, decimal.ROUND_CEILING))


def floor(x, ndigits=0):
    """Round towards -infinity at ndigits."""
    return _apply(x, lambda v: _decimal_round(v, ndigits, decimal.ROUND_FLOOR))


def iferror(series, func, default=None):
    """Apply func to each value; errors give default (Excel IFERROR)."""
    def safe(v):
        try:
            return func(v)
        except Exception:
            return default
    return series.map(safe)


def coalesce(*series):
    """First non-blank value across columns, left to right (blank strings count as blank)."""
    out = None
    for s in series:
        s = s.where(~(s.astype("string").str.strip() == "").fillna(False), np.nan) if not np.isscalar(s) else s
        out = s if out is None else out.where(out.notna(), s)
    return out


def textjoin(df, cols, sep=", ", skip_blank=True):
    """Excel TEXTJOIN across columns per row."""
    def join(row):
        vals = ["" if pd.isna(v) else str(v) for v in row]
        return sep.join(v for v in vals if v != "" or not skip_blank)
    return df[list(cols)].apply(join, axis=1)


def safe_divide(a, b, default=np.nan):
    """a / b with default where b is 0 or blank (no inf, no ZeroDivisionError)."""
    a = pd.Series(a) if np.isscalar(a) else a
    b = pd.Series(b, index=a.index) if np.isscalar(b) else b
    res = a / b.where(b != 0)
    return res.where(b.ne(0) & b.notna(), default)


def edate(dates, months):
    """Excel EDATE: same day n months later; clipped to month end (31 Jan + 1 -> 28/29 Feb)."""
    d = pd.to_datetime(dates)
    return d + pd.DateOffset(months=int(months))


def eomonth(dates, months=0):
    """Excel EOMONTH: last day of the month, n months from dates."""
    d = pd.to_datetime(dates)
    return (d + pd.DateOffset(months=int(months))) + pd.offsets.MonthEnd(0)


def _hol(holidays):
    return np.array(sorted({pd.Timestamp(h).date() for h in holidays}), dtype="datetime64[D]") if holidays else None


def _days(x):
    return pd.to_datetime(pd.Series(x) if np.isscalar(x) or isinstance(x, (list, tuple)) else x).values.astype("datetime64[D]")


def networkdays(start, end, holidays=()):
    """Excel NETWORKDAYS: business days from start to end, both counted; negative if end < start; blank -> <NA>."""
    s, e = _days(start), _days(end)
    bad = np.isnat(s) | np.isnat(e)
    s, e = np.where(bad, np.datetime64("2000-01-03"), s), np.where(bad, np.datetime64("2000-01-03"), e)
    hol = _hol(holidays)
    kw = {"holidays": hol} if hol is not None else {}
    one = np.timedelta64(1, "D")
    out = np.where(e >= s, np.busday_count(s, e + one, **kw), -np.busday_count(e, s + one, **kw))
    res = pd.array(np.where(bad, 0, out), dtype="Int64")
    res[bad] = pd.NA
    return pd.Series(res, index=start.index) if isinstance(start, pd.Series) else res


def workday(start, days, holidays=()):
    """Excel WORKDAY: the date `days` business days after (or before, if negative) start; start not counted."""
    s = _days(start)
    bad = np.isnat(s)
    s = np.where(bad, np.datetime64("2000-01-03"), s)
    hol = _hol(holidays)
    kw = {"holidays": hol} if hol is not None else {}
    days = np.broadcast_to(np.asarray(days), s.shape)
    fwd = np.busday_offset(s, days, roll="backward", **kw)
    back = np.busday_offset(s, days, roll="forward", **kw)
    out = pd.to_datetime(np.where(days > 0, fwd, np.where(days < 0, back, s)))
    out = out.where(~bad, pd.NaT)
    return pd.Series(out, index=start.index) if isinstance(start, pd.Series) else out


def datedif(start, end, unit="D"):
    """Excel DATEDIF: whole 'D' days, 'M' months or 'Y' years between start and end."""
    s, e = pd.to_datetime(start), pd.to_datetime(end)
    if unit.upper() == "D":
        return (e - s).dt.days if isinstance(s, pd.Series) else (e - s).days
    sy, sm, sd = (s.dt.year, s.dt.month, s.dt.day) if isinstance(s, pd.Series) else (s.year, s.month, s.day)
    ey, em, ed = (e.dt.year, e.dt.month, e.dt.day) if isinstance(e, pd.Series) else (e.year, e.month, e.day)
    months = (ey - sy) * 12 + (em - sm) - (ed < sd)
    return months if unit.upper() == "M" else months // 12


_TOKENS = [("yyyy", "%Y"), ("yy", "%y"), ("mmmm", "%B"), ("mmm", "%b"), ("dddd", "%A"), ("ddd", "%a"),
           ("dd", "%d"), ("hh", "%H"), ("ss", "%S"), ("mm", None), ("am/pm", "%p"), ("d", "%d"), ("m", "%m"),
           ("h", "%H")]


def excel_format_to_strftime(fmt):
    """'dd/mm/yyyy hh:mm' -> '%d/%m/%Y %H:%M' ('mm' is minutes after hh or before ss, otherwise month;
    single d/m are zero-padded)."""
    out, i, f = [], 0, fmt
    low = f.lower()
    while i < len(f):
        for tok, rep in _TOKENS:
            if low.startswith(tok, i):
                if tok == "mm":
                    prev = "".join(out).rstrip(":")
                    nxt = low[i + 2:].lstrip(":")
                    rep = "%M" if prev.endswith("%H") or nxt.startswith("ss") else "%m"
                out.append(rep)
                i += len(tok)
                break
        else:
            out.append(f[i])
            i += 1
    return "".join(out)


def excel_text(dates, fmt="dd/mm/yyyy"):
    """Excel TEXT(date, fmt) for a date column."""
    return pd.to_datetime(dates).dt.strftime(excel_format_to_strftime(fmt))


def rank(series, ascending=False, method="min"):
    """Excel RANK.EQ: 1 = largest by default; ties share the best rank."""
    return series.rank(ascending=ascending, method=method).astype("Int64")


def sumproduct(*series):
    """Excel SUMPRODUCT of equally long columns."""
    out = series[0].astype(float)
    for s in series[1:]:
        out = out * s.astype(float)
    return float(out.sum())


def sign(series):
    """-1 / 0 / 1."""
    return np.sign(series)


def left(series, n):
    """Excel LEFT."""
    return series.astype("string").str[:n]


def right(series, n):
    """Excel RIGHT."""
    return series.astype("string").str[-n:] if n else series.astype("string").str[:0]


def mid(series, start, n):
    """Excel MID (start is 1-based)."""
    return series.astype("string").str[start - 1:start - 1 + n]


def proper(series):
    """Excel PROPER: 'ACME pte ltd' -> 'Acme Pte Ltd'."""
    return series.astype("string").str.title()


def isnumber(series):
    """True where the value is (or parses as) a number."""
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce").notna()

