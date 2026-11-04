"""Errors, retry, item loop, checkpoint, dry run, step timer, run folder."""
import functools
import json
import random
import time
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


def retry(max_attempts=3, base_delay=1.0, backoff=2.0, retry_on=(SystemError_, OSError, TimeoutError)):
    """Decorator: retry with exponential backoff + jitter. Use on reads only, never around sends/submits."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*a, **k)
                except retry_on:
                    if attempt == max_attempts:
                        raise
                    time.sleep(base_delay * backoff ** (attempt - 1) * random.uniform(0.7, 1.3))
        return wrapper
    return deco


def process_all(items, handler, max_consecutive_system=3, checkpoint=None, key=str):
    """Run handler(item) for each item. BusinessError -> collected, continue. N SystemErrors in a row -> abort.
    Optional Checkpoint skips items already done. Returns (done_items, failures_df)."""
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
            if isinstance(err, BusinessError):
                failures.append({"item": k, "code": err.code, "message": err.message})
                streak = 0
                if checkpoint is not None:
                    checkpoint.mark_done(k)
            else:
                streak += 1
                if streak >= max_consecutive_system:
                    raise SystemError_("too many consecutive system failures", code="SYSTEM_STREAK", last_item=k) from e
    return done, pd.DataFrame(failures, columns=["item", "code", "message"])


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


def act(fn, *args, dry_run=False, **kwargs):
    """Call a side-effecting function unless dry_run; then only print what would happen."""
    if dry_run:
        print(f"[dry-run] would call {fn.__name__} args={args} kwargs={list(kwargs)}")
        return None
    return fn(*args, **kwargs)


@contextmanager
def step(name, log=print):
    """`with step('load'):` -> START / DONE (ms) / FAIL lines."""
    t = time.perf_counter()
    log(f"START {name}")
    try:
        yield
        log(f"DONE  {name} ({(time.perf_counter() - t) * 1000:.0f} ms)")
    except Exception as e:
        log(f"FAIL  {name}: {classify(e)}")
        raise


def run_dir(robot, business_date=None, root="work"):
    """work/<robot>/<YYYY-MM-DD>/ — logs, checkpoint and outputs for one run."""
    d = Path(root) / robot / (business_date or date.today().isoformat())
    d.mkdir(parents=True, exist_ok=True)
    return d
