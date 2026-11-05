"""Join, lookup, SUMIFS, union, fuzzy match, reconciliation, table diff."""
import difflib
from functools import reduce

import numpy as np
import pandas as pd

from .clean import _INVISIBLE, normalise_name


def _key_text(s, case):
    """Lookup keys as comparable text: whole-number floats without '.0' (an Excel id column with one blank cell
    is float), datetimes in one format, NBSP / repeated spaces collapsed, trimmed, upper-cased unless case."""
    if pd.api.types.is_float_dtype(s):
        nn = s.dropna()
        if len(nn) and np.isfinite(nn).all() and (nn == nn.round()).all():
            s = s.astype("Int64")
    elif pd.api.types.is_datetime64_any_dtype(s):
        s = s.dt.strftime("%Y-%m-%d %H:%M:%S")
    t = s.astype("string").str.replace(_INVISIBLE, " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    return t if case else t.str.upper()


def join3(left, right, left_on, right_on=None, suffixes=("", "_r"), validate=None):
    """Returns (left-only, joined, right-only). validate='many_to_one' raises if right keys repeat."""
    lk = [left_on] if isinstance(left_on, str) else list(left_on)
    rk = lk if right_on is None else ([right_on] if isinstance(right_on, str) else list(right_on))
    J = left.merge(right, left_on=lk, right_on=rk, how="inner", suffixes=suffixes, validate=validate)
    lkeys = pd.MultiIndex.from_frame(left[lk].astype(object))
    rkeys = pd.MultiIndex.from_frame(right[rk].astype(object))
    return left[~lkeys.isin(rkeys)], J, right[~rkeys.isin(lkeys)]


def lookup(df, ref, key, cols, ref_key=None, default=None, as_text=True, allow_duplicates=False, case=False):
    """XLOOKUP: add/overwrite cols from ref by key. Keys compared as trimmed text, ignoring case and repeated
    spaces like Excel, 1234567 == 1234567.0 == '1234567' (case=True to match exactly; as_text=False to compare
    raw values). A blank key never matches. default fills keys not found in ref; a blank value in ref stays
    blank. Raises on duplicate keys in ref (VLOOKUP silently takes the first), naming the spellings that clash."""
    ref_key = ref_key or key
    cols = [cols] if isinstance(cols, str) else list(cols)
    norm = (lambda s: _key_text(s, case)) if as_text else (lambda s: s)
    r = ref.assign(_k=norm(ref[ref_key]))
    r = r[r["_k"].notna()]
    dup = r["_k"].duplicated(keep=False)
    if dup.any() and not allow_duplicates:
        groups = r.loc[dup].groupby("_k", sort=False)[ref_key].agg(lambda x: list(dict.fromkeys(x.astype(str))))
        raise ValueError(f"lookup table has {len(groups)} duplicate keys"
                         + (" after trimming / upper-casing" if as_text and not case else "")
                         + f": {dict(list(groups.items())[:3])}; pass case=True to keep them distinct or "
                         "allow_duplicates=True to take the first")
    r = r.drop_duplicates("_k").set_index("_k")
    out = df.copy()
    k = norm(out[key])
    found = k.notna() & k.isin(r.index)
    for c in cols:
        out[c] = k.map(r[c])
        if default is not None:
            out[c] = out[c].where(found, default)
    return out


def sumifs(df, ref, keys, value_col, agg="sum", name=None, ref_keys=None):
    """SUMIFS / COUNTIFS / AVERAGEIFS: aggregate ref by keys, attach to each df row (0 if none)."""
    keys = [keys] if isinstance(keys, str) else list(keys)
    ref_keys = keys if ref_keys is None else ([ref_keys] if isinstance(ref_keys, str) else list(ref_keys))
    name = name or f"{agg}_{value_col}"
    g = ref.groupby(ref_keys, dropna=False)[value_col].agg(agg).reset_index()
    g.columns = keys + [name]
    out = df.merge(g, on=keys, how="left")
    out[name] = out[name].fillna(0)
    return out


def group_value(df, by, col, agg="sum"):
    """Group aggregate repeated on every row (share-of-total, 'group has >1 row' flags)."""
    return df.groupby(by, dropna=False)[col].transform(agg)


def outer_join(left, right, on_left, on_right=None):
    """Full outer join; '_merge' column says left_only / right_only / both."""
    return left.merge(right, left_on=on_left, right_on=on_right or on_left, how="outer", indicator=True)


def join_multiple(frames, on, how="outer"):
    """Join several tables on the same key."""
    return reduce(lambda a, b: a.merge(b, on=on, how=how), frames)


def union(frames, names=None, source_col="source"):
    """Stack tables by column name (missing columns -> blank); optional source label."""
    if names:
        frames = [f.assign(**{source_col: n}) for f, n in zip(frames, names)]
    return pd.concat(frames, ignore_index=True, sort=False)


def append_fields(df, other):
    """Every row of df with every row of other (cross join; keep `other` small)."""
    return df.merge(other, how="cross")


def fuzzy_match(left, right, threshold=0.85, normalise=normalise_name):
    """Best match in right for each value in left (names, not ids). Returns value, match, score."""
    L = pd.Series(pd.unique(pd.Series(left).dropna()))
    R = pd.Series(pd.unique(pd.Series(right).dropna()))
    r_by_norm = dict(zip(normalise(R).tolist(), R.tolist()))
    choices = list(r_by_norm)
    rows = []
    for orig, key in zip(L.tolist(), normalise(L).tolist()):
        hit = difflib.get_close_matches(key, choices, n=1, cutoff=threshold)
        score = difflib.SequenceMatcher(None, key, hit[0]).ratio() if hit else 0.0
        rows.append({"value": orig, "match": r_by_norm[hit[0]] if hit else None, "score": round(score, 3)})
    return pd.DataFrame(rows, columns=["value", "match", "score"])


def match_with_tolerance(L, B, *, amt_l, amt_r, date_l, date_r, tol_cents=1, window_days=2):
    """Reconcile: L row matches a B row if amount within tol_cents and B date in [L date, +window_days].
    Each B row used once; earliest date then smallest diff wins. Returns (matched, unmatched_L, unmatched_B)."""
    B = B.copy()
    B["_cents"] = (pd.to_numeric(B[amt_r], errors="coerce") * 100).round().astype("Int64")
    B["_used"] = False
    matched, unmatched = [], []
    for i, row in L.iterrows():
        if pd.isna(row[amt_l]) or pd.isna(row[date_l]):
            unmatched.append({"index": i, "reason": "missing amount/date"})
            continue
        target = int(round(row[amt_l] * 100))
        d0, d1 = row[date_l], row[date_l] + pd.Timedelta(days=window_days)
        cand = B[(~B["_used"]) & ((B["_cents"] - target).abs() <= tol_cents).fillna(False) & B[date_r].between(d0, d1)]
        if cand.empty:
            unmatched.append({"index": i, "reason": "no match in window"})
            continue
        j = cand.assign(_d=(cand["_cents"] - target).abs()).sort_values([date_r, "_d"]).index[0]
        B.at[j, "_used"] = True
        matched.append({"index": i, "match_index": j, "diff_cents": int(B.at[j, "_cents"] - target)})
    return (pd.DataFrame(matched, columns=["index", "match_index", "diff_cents"]),
            pd.DataFrame(unmatched, columns=["index", "reason"]),
            B[~B["_used"]].drop(columns=["_cents", "_used"]))


def compare_frames(old, new, key, cols=None):
    """Diff two versions of a table by key -> (added, removed, changed[key, column, old, new])."""
    cols = cols or [c for c in old.columns if c in new.columns and c != key]
    o = old.drop_duplicates(key).set_index(key)
    n = new.drop_duplicates(key).set_index(key)
    added = new[new[key].isin(n.index.difference(o.index))]
    removed = old[old[key].isin(o.index.difference(n.index))]
    common = o.index.intersection(n.index)
    changes = []
    for c in cols:
        a, b = o.loc[common, c].astype(object), n.loc[common, c].astype(object)
        same = (a == b) | (a.isna() & b.isna())
        for k in common[~same.to_numpy(dtype=bool)]:
            changes.append({key: k, "column": c, "old": a[k], "new": b[k]})
    return added, removed, pd.DataFrame(changes, columns=[key, "column", "old", "new"])


def reconcile(left, right, on, compare=None, tolerance=None, suffixes=("_l", "_r"), case_insensitive_text=True):
    """Two-way reconciliation by key. Every key gets status MATCHED / BREAK / LEFT_ONLY / RIGHT_ONLY,
    'break_cols' lists the fields that differ, and '<col>_diff' holds numeric differences.
    tolerance = {'amount': 0.01} (absolute, compared in cents-safe rounding); text compared trimmed."""
    on = [on] if isinstance(on, str) else list(on)
    compare = compare or [c for c in left.columns if c in right.columns and c not in on]
    tolerance = tolerance or {}
    for name, d in (("left", left), ("right", right)):
        dup = d.duplicated(subset=on, keep=False)
        if dup.any():
            raise ValueError(f"{name} has duplicate keys {on}: {d.loc[dup, on].head(3).to_dict('records')}")
    m = left.merge(right, on=on, how="outer", suffixes=suffixes, indicator=True)
    breaks = pd.Series([[] for _ in range(len(m))], index=m.index, dtype=object)
    both = m["_merge"] == "both"
    for c in compare:
        a, b = m[f"{c}{suffixes[0]}"], m[f"{c}{suffixes[1]}"]
        if all(pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x) for x in (a, b)):
            diff = (a - b).round(10)
            m[f"{c}_diff"] = diff
            tol = tolerance.get(c, 0)
            bad = ~((diff.abs() <= tol + 1e-9) | (a.isna() & b.isna()))
        else:
            na, nb = a.astype("string").str.strip(), b.astype("string").str.strip()
            if case_insensitive_text:
                na, nb = na.str.upper(), nb.str.upper()
            bad = ~((na == nb).fillna(False) | (a.isna() & b.isna()))
        bad = bad & both
        for i in m.index[bad.to_numpy(dtype=bool)]:
            breaks.at[i].append(c)
    m["break_cols"] = breaks.map(lambda x: ",".join(x))
    m["status"] = np.select([m["_merge"] == "left_only", m["_merge"] == "right_only", m["break_cols"] != ""],
                            ["LEFT_ONLY", "RIGHT_ONLY", "BREAK"], default="MATCHED")
    return m.drop(columns="_merge")


def recon_summary(recon):
    """Counts per status from reconcile()."""
    return recon["status"].value_counts().rename_axis("status").reset_index(name="count")
