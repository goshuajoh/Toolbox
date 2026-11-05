"""Alteryx-style workflows in pandas: chain steps and see rows in/out + time per step (like the record counts on
Alteryx connectors), keep snapshots to inspect, write intermediate outputs, cache slow inputs while developing."""
import os
import time
from pathlib import Path

import pandas as pd

from .robust import BusinessError


class Flow:
    """Flow("recon").step("load", read_sheet, "in.xlsx").step("fx", lambda d: d[d.desk == "FX"]).run()
    -> output DataFrame; .trace = DataFrame(step, rows_in, rows_out, cols, ms, status, error);
    keep=True stores each step's output in .snapshots."""

    def __init__(self, name="flow", keep=False, log=print):
        self.name, self.keep, self.log = name, keep, log
        self.steps, self.snapshots = [], {}
        self.trace = pd.DataFrame(columns=["step", "rows_in", "rows_out", "cols", "ms", "status", "error"])

    def step(self, name, fn, *args, **kwargs):
        """Add a step: fn(df, *args, **kwargs) -> DataFrame (the first step gets no df if run() had none)."""
        self.steps.append((name, fn, args, kwargs))
        return self

    def check(self, name, predicate, message="check failed"):
        """Stop the flow with a BusinessError when predicate(df) is False, e.g. .check("has rows", lambda d: len(d) > 0)."""
        def _check(df):
            if not predicate(df):
                raise BusinessError(f"{name}: {message}", code="FLOW_CHECK")
            return df
        return self.step(name, _check)

    def run(self, df=None):
        rows, cur = [], df
        try:
            for name, fn, args, kwargs in self.steps:
                n_in, t = (None if cur is None else len(cur)), time.perf_counter()
                try:
                    cur = fn(*args, **kwargs) if cur is None else fn(cur, *args, **kwargs)
                    if not isinstance(cur, pd.DataFrame):
                        raise TypeError(f"step '{name}' returned {type(cur).__name__}, not a DataFrame")
                except Exception as e:
                    rows.append({"step": name, "rows_in": n_in, "rows_out": None, "cols": None,
                                 "ms": round((time.perf_counter() - t) * 1000, 1), "status": "FAILED",
                                 "error": f"{type(e).__name__}: {e}"})
                    self.log(f"{self.name} FAILED at '{name}': {type(e).__name__}: {e}")
                    raise
                ms = (time.perf_counter() - t) * 1000
                rows.append({"step": name, "rows_in": n_in, "rows_out": len(cur), "cols": cur.shape[1],
                             "ms": round(ms, 1), "status": "OK", "error": ""})
                if self.keep:
                    self.snapshots[name] = cur.copy()
                self.log(f"{name}: {n_in if n_in is not None else '-'} -> {len(cur)} rows ({ms:.0f} ms)")
        finally:
            self.trace = pd.DataFrame(rows, columns=self.trace.columns).astype(
                {"rows_in": "Int64", "rows_out": "Int64", "cols": "Int64"})
        return cur


def count_rows(df, label="rows", log=print):
    """Log rows x cols and pass df through: df.pipe(count_rows, "after filter")."""
    log(f"{label}: {len(df):,} rows x {df.shape[1]} cols")
    return df


def tee(df, path, **kw):
    """Write df to path (.csv / .xlsx / .parquet) and pass it through, like an Output tool mid-workflow."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in (".csv", ".xlsx", ".parquet"):
        raise ValueError("tee writes .csv, .xlsx or .parquet")
    p.parent.mkdir(parents=True, exist_ok=True)
    {".parquet": df.to_parquet, ".xlsx": df.to_excel, ".csv": df.to_csv}[ext](p, index=False, **kw)
    return df


def cached(path, fn, *args, refresh=False, **kwargs):
    """Saved result if path exists, else fn(*args, **kwargs) saved to path (.parquet / .pkl / .csv) and read back,
    so the first run and later runs see the same types. refresh=True (or delete the file) rebuilds.
    Speeds up development on slow inputs. Only load files you created."""
    p = Path(path)
    ext = p.suffix.lower()
    readers = {".parquet": pd.read_parquet, ".pkl": pd.read_pickle, ".csv": pd.read_csv}
    if ext not in readers:
        raise ValueError("cache file must end in .parquet, .pkl or .csv")
    if p.exists() and not refresh:
        return readers[ext](p)
    df = fn(*args, **kwargs)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")                          # atomic: an interrupt leaves no half file
    if ext == ".parquet":
        df.to_parquet(tmp, index=False)
    elif ext == ".pkl":
        df.to_pickle(tmp)
    else:
        df.to_csv(tmp, index=False)
    os.replace(tmp, p)
    return readers[ext](p)
