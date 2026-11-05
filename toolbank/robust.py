"""Errors, retry, item loop, checkpoint, dry run, step timer, run folder."""
import functools
import json
import os
import random
import sys
import sysconfig
import time
import traceback
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd


class RobotError(Exception):
    """Base error with a short code; context = ids only, never data."""
    retryable = False

    def __init__(self, message, code="ERROR", **context):
        super().__init__(message)
        self.message, self.code, self.context = message, code, context

    def __str__(self):
        return f"[{self.code}] {self.message} {self.context or ''}".strip()


class BusinessError(RobotError):
    """Data is wrong -> record and skip the item, never retry."""
    retryable = False


class SystemError_(RobotError):
    """Environment failed (file locked, timeout) -> retry, abort if persistent."""
    retryable = True


def classify(exc):
    """Map plain Python errors onto the robot's three kinds: OSError / timeouts -> SystemError_ (retry);
    ValueError / KeyError (what bad data raises) -> BusinessError BAD_DATA; anything else, TypeError and
    AttributeError included (almost always a bug) -> plain RobotError (stop, needs a fix)."""
    if isinstance(exc, RobotError):
        return exc
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return SystemError_(str(exc), code="SYSTEM")
    if isinstance(exc, (ValueError, KeyError)):
        return BusinessError(str(exc), code="BAD_DATA")
    return RobotError(str(exc))


_LIB_DIRS = tuple(os.path.normcase(p) + os.sep for p in
                  {sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")}
                  | {sys.prefix, sys.base_prefix, sys.exec_prefix} if p) + (os.path.normcase(str(Path(__file__).parent)) + os.sep,)


def _is_lib(filename):
    """True for frames of the standard library (also frozen '<frozen os>' / zipped), site-packages or toolbank."""
    fn = os.path.normcase(filename)
    return fn.startswith("<") or fn.startswith(_LIB_DIRS) or "site-packages" in fn or "dist-packages" in fn


def error_where(exc):
    """'function (file.py:123)' of the deepest frame in your own code (not pandas, the standard library or
    toolbank) where exc was raised; the deepest frame of all when none is yours. '' without a traceback."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return ""
    own = [f for f in frames if not _is_lib(f.filename)]
    f = (own or frames)[-1]
    return f"{f.name} ({Path(f.filename).name}:{f.lineno})"


def error_info(exc):
    """Plain dict for logs and exception reports: type, code, message, retryable, where (see error_where),
    context (ids given to RobotError). Works for any exception; plain errors are classified first."""
    err = classify(exc)
    return {"type": type(exc).__name__, "code": err.code, "message": err.message, "retryable": err.retryable,
            "where": error_where(exc), "context": dict(err.context)}


def retry(max_attempts=3, base_delay=1.0, backoff=2.0, retry_on=(SystemError_, OSError, TimeoutError), log=None):
    """Decorator: call the function up to max_attempts times (so 2 retries by default) with exponential backoff
    + jitter. Use on reads only, never around sends/submits. log=print (or logger.warning) reports each retry:
    'retry 1/2 in 2.1s after OSError: ...'."""
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError(f"max_attempts must be a whole number >= 1, not {max_attempts!r}")

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*a, **k)
                except retry_on as e:
                    if attempt == max_attempts:
                        raise
                    delay = base_delay * backoff ** (attempt - 1) * random.uniform(0.7, 1.3)
                    if log is not None:
                        log(f"{fn.__name__}: retry {attempt}/{max_attempts - 1} in {delay:.1f}s after "
                            f"{type(e).__name__}: {str(e)[:200]}")
                    time.sleep(delay)
        return wrapper
    return deco


_FAILURE_COLS = ["item", "code", "message", "type", "where", "retryable"]


def _blank_key(k):
    return k is None or str(k).strip().lower() in ("", "nan", "none", "nat", "<na>")


def process_all(items, handler, max_consecutive_system=3, checkpoint=None, key=str, log=None):
    """Run handler(item) for each item and record what happened, so the robot can report and resume:
    BusinessError (data is wrong) -> recorded with retryable False and skipped; SystemError_ / OSError
    (environment) -> recorded with retryable True, and N in a row -> abort with SYSTEM_STREAK naming the last
    error; any other RobotError or a plain bug (AttributeError, auth failure, bad SQL...) -> stop at once, since
    every item would fail the same way (the error carries item= and where=). Only successes are written to the
    optional Checkpoint, so a re-run retries every failure and reports it again until it is fixed; with a
    checkpoint a blank key or a key repeated in the same run is recorded as MISSING_KEY / DUPLICATE_KEY, never
    silently skipped. log=print reports each failure as it happens.
    Returns (done_items, failures_df) with columns item, code, message, type, where, retryable:
    failures.retryable.any() -> re-run later (exit 3); other failures -> report to the owner (exit 2)."""
    done, failures, streak, seen = [], [], 0, set()

    def record(k, code, message, info=None, retryable=False):
        failures.append({"item": k, "code": code, "message": message, "type": info["type"] if info else "",
                         "where": info["where"] if info else "", "retryable": retryable})
    for item in items:
        k = key(item)
        if checkpoint is not None:
            if _blank_key(k):
                record(k, "MISSING_KEY", "blank key: the item cannot be checkpointed or resumed")
                continue
            if str(k) in seen:
                record(k, "DUPLICATE_KEY", "key already processed in this run: the item cannot be resumed")
                continue
            seen.add(str(k))
            if checkpoint.is_done(k):
                continue
        try:
            handler(item)
            done.append(item); streak = 0
            if checkpoint is not None:
                checkpoint.mark_done(k)
        except Exception as e:
            err = classify(e)
            info = error_info(e)
            if isinstance(err, BusinessError):
                record(k, err.code, err.message, info)
                streak = 0
                if log is not None:
                    log(f"skip {k}: [{err.code}] {err.message} at {info['where']}")
            elif not err.retryable:
                err.context.setdefault("item", k)
                err.context.setdefault("where", info["where"])
                if log is not None:
                    log(f"stop at {k}: [{err.code}] {err.message} at {info['where']}")
                if err is e:
                    raise
                raise err from e
            else:
                record(k, err.code, err.message, info, retryable=True)
                streak += 1
                if log is not None:
                    log(f"system failure {streak}/{max_consecutive_system} at {k}: [{err.code}] {err.message}")
                if streak >= max_consecutive_system:
                    raise SystemError_(f"{streak} consecutive system failures; last: {info['type']}: {err.message}",
                                       code="SYSTEM_STREAK", last_item=k, where=info["where"]) from e
    return done, pd.DataFrame(failures, columns=_FAILURE_COLS)


class Checkpoint:
    """Remember done keys in a JSON file so a re-run resumes. Key = business id, never row number."""

    def __init__(self, path):
        self.path = Path(path)
        self.done = set(json.loads(self.path.read_text())) if self.path.exists() else set()

    def is_done(self, key):
        return str(key) in self.done

    def mark_done(self, key):
        self.done.add(str(key))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.done)))
        tmp.replace(self.path)

    def reset(self):
        self.done.clear()
        self.path.unlink(missing_ok=True)


def act(fn, *args, dry_run=False, log=print, **kwargs):
    """Call a side-effecting function unless dry_run; then only log what would happen (argument names, not values:
    they may hold data)."""
    if dry_run:
        name = getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", None) or repr(fn)
        log(f"[dry-run] would call {name}({len(args)} positional args, kwargs={list(kwargs)})")
        return None
    return fn(*args, **kwargs)


@contextmanager
def step(name, log=print):
    """`with step('load'):` -> START / DONE (ms) / FAIL lines (FAIL names the function and line that raised)."""
    t = time.perf_counter()
    log(f"START {name}")
    try:
        yield
        log(f"DONE  {name} ({(time.perf_counter() - t) * 1000:.0f} ms)")
    except Exception as e:
        log(f"FAIL  {name} ({(time.perf_counter() - t) * 1000:.0f} ms): {classify(e)} at {error_where(e)}")
        raise


def run_dir(robot, business_date=None, root="work"):
    """work/<robot>/<YYYY-MM-DD>/ — logs, checkpoint and outputs for one run."""
    d = Path(root) / robot / (business_date or date.today().isoformat())
    d.mkdir(parents=True, exist_ok=True)
    return d
