"""Filter, formula, multi-row, text split/regex, unique, sample (Alteryx Preparation/Parse tools)."""
import pandas as pd


def filter_split(df, mask):
    """Returns (rows where mask True, rows where False/blank)."""
    m = pd.Series(mask, index=df.index).fillna(False).astype(bool)
    return df[m], df[~m]


def case_when(df, rules, default=None):
    """IF/ELSEIF/ELSE column: rules = [(mask, value_or_series), ...]; first match wins."""
    out = pd.Series(default, index=df.index, dtype=object)
    done = pd.Series(False, index=df.index)
    for mask, value in rules:
        m = pd.Series(mask, index=df.index).fillna(False).astype(bool) & ~done
        out[m] = value[m] if isinstance(value, pd.Series) else value
        done |= m
    return out


def multi_field(df, cols, func):
    """Apply the same function to several columns."""
    out = df.copy()
    for c in cols:
        out[c] = func(out[c])
    return out


def prev_value(df, col, by=None, n=1):
    """Value from n rows above (within group). Sort first."""
    return df.groupby(by)[col].shift(n) if by else df[col].shift(n)


def running_total(df, col, by=None):
    """Cumulative sum (within group). Sort first."""
    return df.groupby(by)[col].cumsum() if by else df[col].cumsum()


def record_id(df, start=1, by=None, name="record_id"):
    """Row number overall or restarting per group."""
    out = df.copy()
    out[name] = (out.groupby(by).cumcount() if by else pd.Series(range(len(out)), index=out.index)) + start
    return out


def tile(series, n=4, labels=None):
    """n equal-count bands (quartiles, deciles)."""
    return pd.qcut(series, q=n, labels=labels, duplicates="drop")


def buckets(series, edges, labels):
    """Fixed bands, e.g. ageing: edges=[-np.inf,0,3,5,10,np.inf], labels=['current','1-3','4-5','6-10','>10']."""
    return pd.cut(series, bins=edges, labels=labels)


def expand_dates(df, start_col, end_col, freq="D", name="date"):
    """One row per day ('B' business day, 'MS' month start) between start and end."""
    out = df.copy()
    out[name] = [pd.date_range(s, e, freq=freq) for s, e in zip(out[start_col], out[end_col])]
    return out.explode(name, ignore_index=True)


def text_to_columns(df, col, sep, names=None, regex=False):
    """Split one column into several."""
    parts = df[col].astype("string").str.split(sep, expand=True, regex=regex)
    if names:
        parts = parts.iloc[:, :len(names)]
        parts.columns = list(names)[:parts.shape[1]]
    else:
        parts.columns = [f"{col}_{i + 1}" for i in range(parts.shape[1])]
    return pd.concat([df, parts], axis=1)


def text_to_rows(df, col, sep):
    """'A;B;C' in one cell -> three rows."""
    out = df.copy()
    out[col] = out[col].astype("string").str.split(sep)
    out = out.explode(col, ignore_index=True)
    out[col] = out[col].astype("string").str.strip()
    return out


def regex_extract(df, col, pattern):
    """Named groups become columns: r'(?P<ccy>[A-Z]{3})\\s*(?P<amt>[\\d,.]+)'."""
    return pd.concat([df, df[col].astype("string").str.extract(pattern)], axis=1)


def regex_flag(series, pattern, case=False):
    """True where text matches the pattern."""
    return series.astype("string").str.contains(pattern, case=case, regex=True, na=False)


def find_duplicates(df, keys):
    """All rows whose key appears more than once (every copy kept, for review)."""
    return df[df.duplicated(subset=keys, keep=False)].sort_values(keys)


def unique_split(df, keys):
    """(first occurrence of each key, the later duplicates)."""
    dup = df.duplicated(subset=keys, keep="first")
    return df[~dup], df[dup]


def keep_latest(df, keys, order_by, ascending=False):
    """One row per key: the latest / highest by order_by."""
    return df.sort_values(order_by, ascending=ascending).drop_duplicates(subset=keys, keep="first")


def sample_rows(df, n, how="first", by=None, seed=42):
    """First / last / random n rows, overall or per group."""
    if how == "random":
        df = df.sample(frac=1, random_state=seed)
        how = "first"
    g = df.groupby(by) if by else None
    if how == "first":
        return g.head(n) if by else df.head(n)
    if how == "last":
        return g.tail(n) if by else df.tail(n)
    raise ValueError(how)


# ==================== SORT ====================

def _natural_key(v):
    import re as _re
    return [int(t) if t.isdigit() else t.lower() for t in _re.split(r"(\d+)", "" if pd.isna(v) else str(v))]


def sort_natural(df, col, ascending=True):
    """'A2' before 'A10' (human order, not text order)."""
    keys = df[col].map(_natural_key)
    order = sorted(range(len(df)), key=lambda i: keys.iloc[i], reverse=not ascending)
    return df.iloc[order]


def sort_custom(df, col, order, then=None):
    """Sort by a business order, e.g. order=['HIGH', 'MEDIUM', 'LOW']; unknown values last; then= extra sort cols."""
    rank = {v: i for i, v in enumerate(order)}
    tmp = df.assign(_ord=df[col].map(lambda v: rank.get(v, len(order))))
    return tmp.sort_values(["_ord"] + list(then or []), kind="stable").drop(columns="_ord")


def top_n(df, n, sort_col, by=None, ascending=False):
    """Top n rows overall or per group by sort_col."""
    d = df.sort_values(sort_col, ascending=ascending, kind="stable")
    return d.groupby(by).head(n) if by else d.head(n)


# ==================== WINDOW (per group) ====================

def next_value(df, col, by=None, n=1):
    """Value from n rows below (within group). Sort first."""
    return df.groupby(by)[col].shift(-n) if by else df[col].shift(-n)


def rank_within(df, col, by=None, ascending=False, method="min"):
    """Rank within group (1 = largest by default)."""
    g = df.groupby(by)[col] if by else df[col]
    return g.rank(ascending=ascending, method=method).astype("Int64")


def rolling_within(df, col, window, by=None, func="mean", min_periods=1):
    """Moving mean/sum/max... over the last `window` rows within group. Sort first."""
    if by:
        return df.groupby(by)[col].transform(lambda s: getattr(s.rolling(window, min_periods=min_periods), func)())
    return getattr(df[col].rolling(window, min_periods=min_periods), func)()


def pct_change_within(df, col, by=None):
    """Change vs previous row within group, as a fraction. Sort first."""
    prev = prev_value(df, col, by)
    return (df[col] - prev) / prev.where(prev != 0)


def nest(df, by, col, name=None):
    """Collapse rows to one per group with a list of values (opposite of text_to_rows / explode)."""
    return df.groupby(by, dropna=False)[col].agg(list).reset_index(name=name or f"{col}_list")


def first_last(df, by, order_col):
    """First and last row of each group by order_col -> (first_rows, last_rows)."""
    d = df.sort_values(order_col, kind="stable")
    return d.groupby(by).head(1), d.groupby(by).tail(1)


def filter_rows(df, *conditions, how="all", case=False):
    """Alteryx Filter with several conditions: filter_rows(df, ("ccy", "in", "USD,SGD"), ("amount", ">", 1e6)).
    how='all' (AND) or 'any' (OR); operators as rules.OPS. Returns (matching, not_matching)."""
    from .rules import condition
    masks = [condition(df, *(list(c) + [None])[:3], case=case) for c in conditions]
    if not masks:
        return df, df.iloc[0:0]
    m = masks[0]
    for x in masks[1:]:
        m = (m & x) if how == "all" else (m | x)
    return df[m], df[~m]


def filter_text(df, text, cols=None, case=False, regex=False):
    """Rows where any of cols (default all) contains text — a search box."""
    m = pd.Series(False, index=df.index)
    for c in (cols or df.columns):
        m |= df[c].astype("string").str.contains(text, case=case, regex=regex, na=False).astype(bool)
    return df[m]


def filter_between(df, col, low=None, high=None):
    """Rows with low <= col <= high; either end optional ('2026-01-31' works on date columns)."""
    s = df[col]
    conv = pd.Timestamp if pd.api.types.is_datetime64_any_dtype(s) else (lambda v: v)
    m = s.notna()
    if low is not None:
        m &= s >= conv(low)
    if high is not None:
        m &= s <= conv(high)
    return df[m]
