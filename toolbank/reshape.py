"""Summarize, pivot, unpivot, subtotals (Alteryx Summarize / Cross Tab / Transpose)."""
import pandas as pd


def summarize(df, by, **aggs):
    """Group and aggregate: summarize(df, ['ccy'], total=('amount','sum'), n=('ref','count')). A ['amount', 'sum']
    list (as a YAML recipe gives it) works like the tuple."""
    aggs = {k: tuple(v) if isinstance(v, list) else v for k, v in aggs.items()}
    return df.groupby(by, dropna=False).agg(**aggs).reset_index()


def pivot(df, index, columns, values, aggfunc="sum", totals=True, fill=0):
    """Excel-style pivot with optional Total row/column; flat column names ('v | USD' for several values)."""
    p = pd.pivot_table(df, index=index, columns=columns, values=values, aggfunc=aggfunc,
                       fill_value=fill, margins=totals, margins_name="Total", observed=True)
    p.columns = [" | ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in p.columns]
    return p.reset_index()


def unpivot(df, id_cols, var_name="field", value_name="value"):
    """Wide -> long (months across -> one row per month)."""
    return df.melt(id_vars=id_cols, var_name=var_name, value_name=value_name)


def add_subtotals(df, group_col, value_cols, label="Subtotal", grand_total=True):
    """Subtotal row after each group + Grand Total row (report layout)."""
    parts = []
    for g, chunk in df.groupby(group_col, sort=False, dropna=False):
        parts += [chunk, pd.DataFrame([{group_col: f"{g} {label}", **{c: chunk[c].sum() for c in value_cols}}])]
    if grand_total:
        parts.append(pd.DataFrame([{group_col: "Grand Total", **{c: df[c].sum() for c in value_cols}}]))
    return pd.concat(parts, ignore_index=True)[list(df.columns)]


def share_of_total(df, col, by=None):
    """Each row as a fraction of the (group) total."""
    return df[col] / (df.groupby(by)[col].transform("sum") if by else df[col].sum())
