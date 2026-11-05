"""Errors, retry, item loop, checkpoint, dry run, step timer, run folder."""
import functools
import json
import random
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
    """Map plain Python errors onto BusinessError / SystemError_."""
    if isinstance(exc, RobotError):
        return exc
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return SystemError_(str(exc), code="SYSTEM")
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return BusinessError(str(exc), code="BAD_DATA")
    return RobotError(str(exc))


_LIB_DIRS = tuple(p for p in {sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")}
                  if p) + (str(Path(__file__).parent),)


def error_where(exc):
    """'function (file.py:123)' of the deepest frame in your own code (not pandas, the standard library or
    toolbank) where exc was raised; the deepest frame of all when none is yours. '' without a traceback."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return ""
    own = [f for f in frames if not f.filename.startswith(_LIB_DIRS) and "site-packages" not in f.filename]
    f = (own or frames)[-1]
    return f"{f.name} ({Path(f.filename).name}:{f.lineno})"


def error_info(exc):
    """Plain dict for logs and exception reports: type, code, message, retryable, where (see error_where),
    context (ids given to RobotError). Works for any exception; plain errors are classified first."""
    err = classify(exc)
    return {"type": type(exc).__name__, "code": err.code, "message": err.message, "retryable": err.retryable,
            "where": error_where(exc), "context": dict(err.context)}


def retry(max_attempts=3, base_delay=1.0, backoff=2.0, retry_on=(SystemError_, OSError, TimeoutError), log=None):
    """Decorator: retry with exponential backoff + jitter. Use on reads only, never around sends/submits.
    log=print (or logger.warning) reports each retry: 'retry 2/3 in 2.1s after OSError: ...'."""
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


def process_all(items, handler, max_consecutive_system=3, checkpoint=None, key=str, log=None):
    """Run handler(item) for each item. BusinessError -> recorded, continue. SystemError_ (and OSError etc.)
    -> continue, but N in a row -> abort with SYSTEM_STREAK naming the last error. Any other RobotError or a
    plain bug (AttributeError, auth failure, bad SQL...) -> stop at once, since every item would fail the same
    way; the error carries item=<key>. Optional Checkpoint skips items already done. log=print reports each
    failure as it happens. Returns (done_items, failures_df) with columns item, code, message, type, where."""
    done, failures, streak = [], [], 0
    for item in items:
        k = key(item)
        if checkpoint is not None and checkpoint.is_done(k):
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
                failures.append({"item": k, "code": err.code, "message": err.message, "type": info["type"],
                                 "where": info["where"]})
                streak = 0
                if log is not None:
                    log(f"skip {k}: [{err.code}] {err.message} at {info['where']}")
                if checkpoint is not None:
                    checkpoint.mark_done(k)
            elif not err.retryable:
                err.context.setdefault("item", k)
                if log is not None:
                    log(f"stop at {k}: [{err.code}] {err.message} at {info['where']}")
                if err is e:
                    raise
                raise err from e
            else:
                streak += 1
                if log is not None:
                    log(f"system failure {streak}/{max_consecutive_system} at {k}: [{err.code}] {err.message}")
                if streak >= max_consecutive_system:
                    raise SystemError_(f"{streak} consecutive system failures; last: {info['type']}: {err.message}",
                                       code="SYSTEM_STREAK", last_item=k, where=info["where"]) from e
    return done, pd.DataFrame(failures, columns=["item", "code", "message", "type", "where"])


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
        log(f"[dry-run] would call {fn.__name__}({len(args)} positional args, kwargs={list(kwargs)})")
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
