"""Profiling, schema checks, rule validation, control totals."""
import pandas as pd


def profile(df):
    """Per column: type, nulls, distinct, min, max, longest text."""
    rows = []
    for c in df.columns:
        s = df[c]
        ordered = pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s)
        rows.append({"column": c, "dtype": str(s.dtype), "rows": len(s), "nulls": int(s.isna().sum()),
                     "distinct": int(s.nunique()),
                     "min": s.min() if ordered else None, "max": s.max() if ordered else None,
                     "max_len": int(s.dropna().astype(str).str.len().max()) if s.notna().any() else 0})
    return pd.DataFrame(rows)


def check_schema(df, expected):
    """expected = {'amount': 'number', 'value_date': 'date', 'ref': 'text', 'x': 'any'}; returns problems ([] = OK)."""
    kinds = {"number": pd.api.types.is_numeric_dtype, "date": pd.api.types.is_datetime64_any_dtype,
             "text": lambda s: s.dtype == object or pd.api.types.is_string_dtype(s), "any": lambda s: True}
    probs = [f"missing column {c}" for c in expected if c not in df.columns]
    probs += [f"{c} should be {k}, is {df[c].dtype}" for c, k in expected.items()
              if c in df.columns and not kinds[k](df[c])]
    return probs


def validate(df, rules, key_col):
    """rules = {'MISSING_AMOUNT': lambda d: d['amount'].isna(), ...} (vectorised masks).
    Returns one row per failure: key, code."""
    out = []
    for code, f in rules.items():
        mask = pd.Series(f(df), index=df.index).fillna(False).astype(bool)
        out.append(pd.DataFrame({"key": df.loc[mask, key_col].to_numpy(), "code": code}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["key", "code"])


def control_totals(df, amount_col, key_col=None):
    """Rows, sum, absolute sum (and distinct keys) — compare input vs output."""
    d = {"rows": len(df), "sum": round(float(df[amount_col].sum()), 2),
         "abs_sum": round(float(df[amount_col].abs().sum()), 2)}
    if key_col:
        d["distinct_keys"] = int(df[key_col].nunique())
    return d


def assert_totals_match(a, b, tol=0.005):
    """Raise if two control_totals differ (counts exact, sums within tol)."""
    for k in a:
        if k in ("rows", "distinct_keys") and a[k] != b.get(k):
            raise ValueError(f"{k}: {a[k]} vs {b.get(k)}")
        if k in ("sum", "abs_sum") and abs(a[k] - b.get(k, 0)) > tol:
            raise ValueError(f"{k}: {a[k]} vs {b.get(k)}")


def assert_unique(df, keys):
    """Raise if key columns have duplicates (shows the first few)."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    dup = df.duplicated(subset=keys, keep=False)
    if dup.any():
        raise ValueError(f"{int(dup.sum())} rows share keys {keys}, e.g. {df.loc[dup, keys].head(3).to_dict('records')}")


def row_hash(df, cols=None):
    """SHA-256 per row over cols (all by default) — detect changed rows between runs, prove what was processed."""
    import hashlib
    cols = list(cols or df.columns)
    text = df[cols].astype("string").fillna("<NA>").agg("\x1f".join, axis=1)
    return text.map(lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()[:16])


def add_audit_columns(df, run_id, source=None, cols=None):
    """Add run_id, processed_at (UTC), source and row_hash columns for evidence."""
    from datetime import datetime, timezone
    out = df.copy()
    out["row_hash"] = row_hash(df, cols)
    out["run_id"] = run_id
    out["processed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if source is not None:
        out["source"] = source
    return out


def changed_rows(old, new, key, cols=None):
    """Rows of new that are added or whose content changed vs old (by row_hash), keyed by key."""
    cols = cols or [c for c in new.columns if c in old.columns]
    o = old.assign(_h=row_hash(old, cols)).set_index(key)["_h"]
    n = new.assign(_h=row_hash(new, cols))
    return n[n[key].map(o).ne(n["_h"]) | ~n[key].isin(o.index)].drop(columns="_h")
