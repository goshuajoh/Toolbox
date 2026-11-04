"""Speed and size: parallel work for waiting-heavy jobs (API calls, downloads, many files), chunks, timing,
progress lines with ETA, smaller DataFrames."""
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager

import pandas as pd


def parallel_map(fn, items, workers=4, processes=False):
    """fn(item) for every item using a pool -> [{'item','ok','result','error'}] in input order; one failure
    never stops the rest. Threads (default) for waiting work; processes=True for heavy CPU work (fn must live in
    a .py module, and on Windows the caller needs `if __name__ == "__main__":`)."""
    items = list(items)
    out = [None] * len(items)
    pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) if processes \
        else ThreadPoolExecutor(workers)                 # spawn = same behaviour as Windows everywhere
    with pool:
        futures = {pool.submit(fn, it): i for i, it in enumerate(items)}
        for f in as_completed(futures):
            i = futures[f]
            try:
                out[i] = {"item": items[i], "ok": True, "result": f.result(), "error": ""}
            except Exception as e:
                out[i] = {"item": items[i], "ok": False, "result": None, "error": f"{type(e).__name__}: {e}"}
    return out


def chunks(seq, size):
    """Consecutive lists of `size` items (last one shorter)."""
    batch = []
    for x in seq:
        batch.append(x)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


@contextmanager
def timer(label="block", log=print):
    """`with timer("load"):` logs elapsed ms; the yielded dict gets 'ms' at the end."""
    out, t = {"label": label}, time.perf_counter()
    try:
        yield out
    finally:
        out["ms"] = round((time.perf_counter() - t) * 1000, 1)
        log(f"{label}: {out['ms']:.0f} ms")


def progress(iterable, total=None, every=100, label="", log=print, clock=time.monotonic):
    """Pass items through, logging 'label 300/1,000 (30%) 52/s eta 0:13' every `every` items."""
    total = total if total is not None else (len(iterable) if hasattr(iterable, "__len__") else None)
    start, n = clock(), 0
    for n, x in enumerate(iterable, 1):
        yield x
        if n % every == 0 or n == total:
            rate = n / max(clock() - start, 1e-9)
            if total:
                eta = int((total - n) / rate) if rate else 0
                log(f"{label} {n:,}/{total:,} ({n / total:.0%}) {rate:.0f}/s eta {eta // 60}:{eta % 60:02d}".strip())
            else:
                log(f"{label} {n:,} {rate:.0f}/s".strip())


def memory_mb(df):
    """Memory used by a DataFrame in MB (deep: counts text)."""
    return round(df.memory_usage(deep=True).sum() / 1e6, 2)


def shrink(df, category_ratio=0.5, ints=True):
    """Smaller DataFrame, no data change: integers downcast, text columns with few distinct values -> category.
    Floats are left alone (money stays exact to the same precision)."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if ints and pd.api.types.is_integer_dtype(s) and not isinstance(s.dtype, pd.api.extensions.ExtensionDtype):
            out[c] = pd.to_numeric(s, downcast="integer")
        elif (s.dtype == object or pd.api.types.is_string_dtype(s)) and len(s) and \
                s.nunique(dropna=True) / len(s) <= category_ratio:
            out[c] = s.astype("category")
    return out
