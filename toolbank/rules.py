"""Config-driven conditions and flags. Business owners keep rules in an Excel sheet (code | column | op | value);
the robot applies them without code changes. Also feature flags and truthy parsing."""
import os
import re
import warnings

import numpy as np
import pandas as pd

from .clean import to_date

OPS = ("==", "!=", ">", ">=", "<", "<=", "in", "not in", "contains", "not contains", "startswith", "endswith",
       "regex", "is null", "not null", "between", "is true", "is false")
_ALIASES = {"=": "==", "eq": "==", "equals": "==", "is": "==", "ne": "!=", "<>": "!=", "not equals": "!=",
            "is not": "!=", "gt": ">", "greater than": ">", "above": ">", "ge": ">=", "gte": ">=", "at least": ">=",
            "lt": "<", "less than": "<", "below": "<", "le": "<=", "lte": "<=", "at most": "<=", "one of": "in",
            "not one of": "not in", "isnull": "is null", "is blank": "is null", "blank": "is null",
            "notnull": "not null", "not blank": "not null", "is not null": "not null", "is not blank": "not null",
            "like": "contains", "includes": "contains", "starts with": "startswith", "ends with": "endswith",
            "matches": "regex"}
_TRUE = {"y", "yes", "true", "t", "1", "on", "x", "✓"}
_FALSE = {"n", "no", "false", "f", "0", "off", ""}


def truthy(v):
    """'Y'/'Yes'/'TRUE'/'1'/'on'/'x' -> True, 'N'/'No'/'0'/''/None -> False."""
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return False
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return v != 0                                  # Excel 1 / 1.0 / 0
    return str(v).strip().lower() in _TRUE


def feature_flag(name, cfg=None, default=False, env_prefix="FLAG_"):
    """On/off switch: env var FLAG_<NAME> beats cfg[name] beats default (e.g. FLAG_SEND_EMAIL=0 for a test run)."""
    env = os.environ.get(f"{env_prefix}{name.upper()}")
    if env is not None:
        return truthy(env)
    if cfg and name in cfg:
        return truthy(cfg[name])
    return default


_NEEDS_VALUE = {">", ">=", "<", "<=", "in", "not in", "contains", "not contains", "startswith", "endswith", "regex",
                "between"}


def _blank_value(value):
    return value is None or (isinstance(value, float) and np.isnan(value)) or \
        (isinstance(value, str) and not value.strip())


def _active(v):
    """True / False for an 'active' cell: blank counts as active; anything not clearly yes or no is None."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return True
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return {1: True, 0: False}.get(v)
    t = str(v).strip().lower()
    t = t[:-2] if t.endswith(".0") else t
    return True if t in _TRUE else False if t in _FALSE else None


def _is_text(s):
    return s.dtype == object or pd.api.types.is_string_dtype(s)


def _cast(series, value):
    """Cast a rule value (usually text typed into Excel) to the column's type. A value the column's type cannot
    read (text for a number or date column) raises ValueError: a rule that silently never matches is worse."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if pd.api.types.is_bool_dtype(series):
        return truthy(value)
    if pd.api.types.is_numeric_dtype(series):
        text = str(value).strip()
        if "," in text and not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", text):
            raise ValueError(f"rule value {value!r} is ambiguous: type 1.5 or 1500 (a comma only between thousands)")
        try:
            return float(text.replace(",", ""))
        except ValueError:
            raise ValueError(f"rule value {value!r} is not a number, but the column holds numbers") from None
    if pd.api.types.is_datetime64_any_dtype(series):
        d = to_date(pd.Series([value])).iloc[0]
        if pd.isna(d):
            raise ValueError(f"rule value {value!r} is not a date, but the column holds dates")
        return d
    return str(value).strip() if isinstance(value, str) else value


def _list(value, numeric=False):
    """A list rule value: a list, or text split on ';' if it has one, else on ','. For a number column, pieces that
    look like thousands groups ('1,000, 2,000') raise ValueError: separate such values with ';'."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    text = str(value)
    parts = [v.strip() for v in text.split(";" if ";" in text else ",")]
    if numeric and ";" not in text and re.search(r"\d,\d{3}(?!\d)", text):           # '1,000' inside the list
        raise ValueError(f"{text!r} looks like numbers with thousands commas: separate the values with ';'")
    return parts


def condition(df, column, op, value=None, case=False):
    """Boolean mask for one condition, e.g. condition(df, 'ccy', 'in', 'USD,SGD'). Blanks (NA, '' or spaces)
    never match, also not 'not in' / '!=' / 'not contains' / 'is false'; only 'is null' matches them. Text
    comparisons ignore case unless case=True. Lists ('in', 'between') split on ',' or, if present, on ';' (use ';'
    for numbers with thousands commas: '1,000; 2,000'). A value the column cannot read (text for a number or date
    column) raises ValueError instead of never matching. Ops: see OPS."""
    if column not in df.columns:
        raise KeyError(f"rule column {column!r} not in data; columns are {list(df.columns)}")
    s = df[column]
    op = _ALIASES.get(str(op).strip().lower(), str(op).strip().lower())
    text = _is_text(s)
    if op in _NEEDS_VALUE and _blank_value(value):
        raise ValueError(f"operator {op!r} needs a value (blank in the rule)")
    norm = (lambda x: x.astype("string").str.strip().str.upper()) if text and not case else (lambda x: x)
    blank = (s.isna() | (s.astype("string").str.strip() == "").fillna(False)) if text else s.isna()
    if op == "is null":
        m = blank
    elif op == "not null":
        m = ~blank
    elif op in ("is true", "is false"):
        t = s.map(truthy)
        m = t if op == "is true" else ~t & ~blank
    elif op in ("in", "not in"):
        vals = [_cast(s, v) for v in _list(value, numeric=not text)]
        if text and not case:
            vals = [str(v).upper() for v in vals]
        m = norm(s).isin(vals) & ~blank
        m = m if op == "in" else (~m & ~blank)
    elif op == "between":
        bounds = _list(value, numeric=not text)
        if len(bounds) != 2:
            raise ValueError(f"'between' needs two values, low and high, e.g. '100; 200'; got {value!r}")
        lo, hi = [_cast(s, v) for v in bounds]
        if lo > hi:
            raise ValueError(f"'between' low {bounds[0]!r} is above high {bounds[1]!r}")
        m = s.between(lo, hi)
    elif op in ("contains", "not contains", "startswith", "endswith", "regex"):
        t = s.astype("string")
        v = str(value)
        if op in ("contains", "not contains"):
            m = t.str.contains(v, case=case, regex=False)
        elif op == "regex":
            with warnings.catch_warnings():                     # groups in the pattern are fine here
                warnings.simplefilter("ignore", UserWarning)
                m = t.str.contains(v, case=case, regex=True)
        else:
            tt, vv = (t, v) if case else (t.str.upper(), v.upper())
            m = tt.str.startswith(vv) if op == "startswith" else tt.str.endswith(vv)
        m = m.fillna(False)
        if op == "not contains":
            m = ~m & ~blank
    elif op in ("==", "!=", ">", ">=", "<", "<="):
        v = _cast(s, value)
        if v is None:
            m = blank if op == "==" else ~blank
        else:
            left, right = (norm(s), str(v).upper()) if text and not case and op in ("==", "!=") else (s, v)
            ops = {"==": lambda: left == right, "!=": lambda: left != right, ">": lambda: s > v,
                   ">=": lambda: s >= v, "<": lambda: s < v, "<=": lambda: s <= v}
            try:
                m = ops[op]()                              # only the requested comparison
            except TypeError as e:
                raise ValueError(f"cannot compare column {column!r} ({s.dtype}) {op} {value!r}") from e
            if op == "!=":
                m = m & ~blank
    else:
        raise ValueError(f"unknown operator {op!r}; use one of {OPS}")
    return pd.Series(m, index=df.index).fillna(False).astype(bool)


def _rules_frame(rules):
    r = rules.copy() if isinstance(rules, pd.DataFrame) else pd.DataFrame(list(rules))
    r.columns = [str(c).strip().lower() for c in r.columns]
    missing = [c for c in ("code", "column", "op") if c not in r.columns]
    if missing:
        raise ValueError(f"rules need columns code, column, op (+ value); missing {missing}")
    if "value" not in r.columns:
        r["value"] = None
    r = r[r[["code", "column", "op"]].notna().any(axis=1).astype(bool)]      # skip fully blank lines
    if "active" in r.columns:
        act = r["active"].map(_active)
        unclear = act.isna()
        if unclear.any():                                       # 'Active', 'Live': never switch a rule off silently
            raise ValueError(f"rules rows {[int(i) + 2 for i in r.index[unclear.to_numpy(dtype=bool)]]} have active "
                             f"{r.loc[unclear, 'active'].tolist()}: type Y or N")
        r = r[act.astype(bool)]
    partial = r[["code", "column", "op"]].isna().any(axis=1)
    if partial.any():                                           # never drop a half-filled active rule silently
        raise ValueError(f"rules rows {[int(i) + 2 for i in r.index[partial]]} miss code/column/op "
                         "(in Excel an operator starting with '=' becomes a formula: use eq, ne, gt, ge, lt, le)")
    return r


def validate_rules(rules, df=None):
    """Problems with a rules table ([] = OK): unknown operators, columns missing from df."""
    r = _rules_frame(rules)
    probs = []
    for _, row in r.iterrows():
        op = _ALIASES.get(str(row["op"]).strip().lower(), str(row["op"]).strip().lower())
        if op not in OPS:
            probs.append(f"{row['code']}: unknown operator {row['op']!r} (in Excel an operator starting with '=' "
                         "becomes a formula: type eq, ne, gt, ge, lt, le)")
        if df is not None and row["column"] not in df.columns:
            probs.append(f"{row['code']}: column {row['column']!r} not in data")
        elif op in OPS:
            try:                                                # the value against the column's type, no rows
                condition(df.iloc[:0] if df is not None else pd.DataFrame({row["column"]: pd.Series(dtype=object)}),
                          row["column"], op, row["value"])
            except ValueError as e:
                probs.append(f"{row['code']}: {e}")
    return probs


def apply_rules(df, rules, prefix="flag_", flags_col="flags", case=False):
    """Apply a rules table: columns code, column, op, value (+ optional active, severity, description).
    Rows with the same code are ANDed. Adds one bool column per code, 'flags' ('CODE1;CODE2') and 'flags_count'."""
    r = _rules_frame(rules)
    out = df.copy()
    codes = list(dict.fromkeys(r["code"].astype(str)))
    for code in codes:
        mask = pd.Series(True, index=df.index)
        for _, row in r[r["code"].astype(str) == code].iterrows():
            mask &= condition(df, row["column"], row["op"], row["value"], case=case)
        out[f"{prefix}{code}"] = mask
    labels = pd.Series("", index=df.index, dtype=object)
    for code in codes:
        labels = labels + np.where(out[f"{prefix}{code}"], code + ";", "")
    out[flags_col] = labels.str.rstrip(";")
    out[f"{flags_col}_count"] = out[[f"{prefix}{c}" for c in codes]].sum(axis=1) if codes else 0
    return out


def rules_from_excel(path, sheet=0):
    """Load a rules sheet (code | column | op | value | active | description) and check its operators.
    Type operators as words in Excel (eq, ne, gt, ge, lt, le, in, contains, is blank): '=' starts a formula."""
    r = pd.read_excel(path, sheet_name=sheet, dtype=str)        # values stay as typed (1 not 1.0)
    probs = validate_rules(r)
    if probs:
        raise ValueError("bad rules: " + "; ".join(probs))
    return _rules_frame(r)


def flag_summary(df, prefix="flag_"):
    """Count of rows per flag, highest first."""
    cols = [c for c in df.columns if str(c).startswith(prefix) and pd.api.types.is_bool_dtype(df[c])]
    return (df[cols].sum().rename(lambda c: c[len(prefix):]).sort_values(ascending=False)
            .rename_axis("code").reset_index(name="rows"))


def first_match(df, rules_in_order, default=None, case=False):
    """Decision table: first rule whose conditions all hold gives the value.
    rules_in_order = [(value, [(column, op, value), ...]), ...]."""
    out = pd.Series(default, index=df.index, dtype=object)
    done = pd.Series(False, index=df.index)
    for result, conds in rules_in_order:
        m = pd.Series(True, index=df.index)
        for col, op, val in conds:
            m &= condition(df, col, op, val, case=case)
        m &= ~done
        out[m] = result
        done |= m
    return out
