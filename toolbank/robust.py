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
                        from .privacy import redact
                        log(redact(f"{fn.__name__}: retry {attempt}/{max_attempts - 1} in {delay:.1f}s after "
                            f"{type(e).__name__}: {str(e)[:200]}"))
                    time.sleep(delay)
        return wrapper
    return deco


# How many tries, how long to wait: one reviewed place instead of numbers scattered through robots.
# Use as attempt(action, verify, **RETRY_PROFILES["ui"]); copy and change a profile per robot if a screen needs it.
RETRY_PROFILES = {
    "ui": dict(tries=3, wait_s=2.0, verify_timeout_s=10.0),           # click / type / select: screens lag, pop-ups
    "page_load": dict(tries=2, wait_s=5.0, verify_timeout_s=60.0),    # open a screen, navigate, search results
    "file_lock": dict(tries=5, wait_s=10.0, backoff=2.0, verify_timeout_s=0.0),  # a file someone has open
    "read": dict(tries=3, wait_s=2.0, backoff=2.0, verify_timeout_s=0.0),        # API GET, query, download
    # post / pay / send: runs once; a second try or a fallback only with already_done=, and an unconfirmed
    # outcome stops the run (STEP_UNCONFIRMED) instead of being re-run
    "submit": dict(tries=1, verify_timeout_s=30.0, poll_s=5.0, irreversible=True),
    "login": dict(tries=1, verify_timeout_s=30.0, poll_s=1.0),       # a repeated wrong password locks the account
}

_BUGS = (TypeError, AttributeError, NameError, AssertionError, ImportError, NotImplementedError)


def _truth(result, what):
    """True / False from what a check returned; an empty DataFrame or Series is False."""
    if isinstance(result, (pd.DataFrame, pd.Series)):
        return not result.empty
    try:
        return bool(result)
    except Exception as e:
        raise TypeError(f"{what} must return True or False, not {type(result).__name__}") from e


def _wait_for(check, timeout_s, poll_s, sleep, clock, what="verify"):
    """True once check() is true, None at the timeout. An exception from check counts as 'not yet'."""
    deadline = clock() + max(0.0, timeout_s)
    while True:
        try:
            result = check()
        except Exception:                                 # element not there yet, window still opening...
            result = None
        if _truth(result, what):
            return True
        if clock() >= deadline:
            return None
        sleep(min(poll_s, max(0.0, deadline - clock())))


def attempt(action, verify=None, *, tries=3, wait_s=2.0, backoff=1.0, verify_timeout_s=10.0, poll_s=0.5,
            recover=None, fallbacks=(), already_done=None, irreversible=False, on_give_up=None, name=None, log=print,
            sleep=time.sleep, clock=time.monotonic):
    """One robot step done safely: action(), then wait until verify() is true (the screen / file / record shows it
    worked). On a failure: recover() (e.g. Esc, close a pop-up, re-focus the window), wait, try again, up to `tries`;
    then each of `fallbacks` (another way to do the same thing: keyboard instead of mouse, API instead of screen)
    once; then on_give_up() (screenshot, close the app) and SystemError_ STEP_FAILED with every try in
    .context['history'] (process_all counts it towards its stop-after-N rule; the template exits 3).
    What is retried: errors from the screen, file or network. Not retried: BusinessError (data is wrong), a
    non-retryable RobotError (AUTH), a bug (TypeError, AttributeError...: raised at once, exit 5) and KeyError /
    ValueError (classified as BusinessError BAD_DATA).
    Anything that must not happen twice (post, pay, send) is irreversible=True (RETRY_PROFILES['submit']): it runs
    once unless already_done is given, and if it cannot be confirmed it raises RobotError STEP_UNCONFIRMED, which
    stops the run (not re-run: a person checks the target). already_done() is called before every try, the first
    too; it returns True / False and raises only when it cannot tell, which stops the step (DONE_CHECK_FAILED)
    instead of repeating it. verify / already_done may return a DataFrame (empty = no). Log lines are redacted.
    Returns action()'s result (None when already done). Profiles: attempt(click, verify, **RETRY_PROFILES['ui'])."""
    from .privacy import redact
    if not isinstance(tries, int) or isinstance(tries, bool) or tries < 1:
        raise ValueError(f"tries must be a whole number >= 1, not {tries!r}")
    if not poll_s > 0 or wait_s < 0 or verify_timeout_s < 0 or backoff < 0:
        raise ValueError("poll_s must be > 0, and wait_s, verify_timeout_s and backoff >= 0")
    fallbacks = [fallbacks] if callable(fallbacks) else list(fallbacks or ())
    for what, fn in [("action", action), ("verify", verify), ("recover", recover), ("already_done", already_done),
                     ("on_give_up", on_give_up)] + [("fallback", f) for f in fallbacks]:
        if fn is not None and not callable(fn):
            raise TypeError(f"{what} must be a function (e.g. lambda: ...), not {type(fn).__name__}")
    if irreversible and already_done is None and (tries > 1 or fallbacks):
        raise ValueError("an irreversible step may run a second time only with already_done= (check the target "
                         "first); otherwise use tries=1 and no fallbacks")
    name = name or getattr(action, "__name__", None) or "step"
    say = (lambda m: log(redact(m))) if log is not None else (lambda m: None)
    routes = [("primary", action, n) for n in range(1, tries + 1)] + \
             [(f"fallback {i}", fb, 1) for i, fb in enumerate(fallbacks, start=1)]
    history, delay = [], wait_s

    def done_already(route, n):
        try:
            return _truth(already_done(), "already_done")
        except (BusinessError, *_BUGS):
            raise
        except RobotError as e:
            if not e.retryable:
                raise
            err = e
        except Exception as e:
            err = e
        history.append({"route": route, "try": n, "outcome": "done check failed",
                        "error": f"{type(err).__name__}: {err}"[:300]})
        raise SystemError_(f"{name}: cannot tell whether it was already done ({type(err).__name__}); not repeated",
                           code="DONE_CHECK_FAILED", step=name, history=history) from err

    for k, (route, fn, n) in enumerate(routes):
        if already_done is not None and done_already(route, n):
            history.append({"route": route, "try": n, "outcome": "already done", "error": ""})
            say(f"{name}: already done, not repeated")
            return None
        try:
            result = fn()
        except BusinessError:
            raise
        except RobotError as e:
            if not e.retryable:
                raise
            history.append({"route": route, "try": n, "outcome": "error", "error": f"{type(e).__name__}: {e}"[:300]})
        except (*_BUGS, KeyError, ValueError) as e:
            if irreversible and already_done is None:         # it may have gone out before the error: a person checks
                raise RobotError(f"{name}: {type(e).__name__} after the step started; it may have happened: check the "
                                 "target before running it again", code="STEP_UNCONFIRMED", step=name,
                                 history=history) from e
            if isinstance(e, _BUGS):
                raise                                         # a bug: every try would fail the same way
            raise classify(e) from e                          # bad data, not a slow screen
        except Exception as e:
            history.append({"route": route, "try": n, "outcome": "error", "error": f"{type(e).__name__}: {e}"[:300]})
        else:
            if verify is None or _wait_for(verify, verify_timeout_s, poll_s, sleep, clock):
                if k:
                    say(f"{name}: ok on {route} try {n}")
                return result
            history.append({"route": route, "try": n, "outcome": "not verified",
                            "error": f"check not true within {verify_timeout_s:g}s"})
        last = history[-1]
        say(f"{name}: {route} try {n} failed ({last['outcome']}: {last['error']})")
        if k == len(routes) - 1:
            break
        if recover is not None:
            try:
                recover()
            except Exception as e:                            # a failed clean-up must not hide the real problem
                say(f"{name}: recover failed: {type(e).__name__}: {e}")
        if delay:
            sleep(delay)
        delay *= backoff
    if on_give_up is not None:
        try:
            on_give_up()
        except Exception as e:
            say(f"{name}: on_give_up failed: {type(e).__name__}: {e}")
    last = history[-1]
    if irreversible and already_done is None:
        raise RobotError(f"{name}: not confirmed; it may have happened: check the target before running it again "
                         f"(last: {last['error']})", code="STEP_UNCONFIRMED", step=name, history=history)
    raise SystemError_(f"{name}: failed after {tries} tries and {len(fallbacks)} fallbacks; last: {last['error']}",
                       code="STEP_FAILED", step=name, history=history)


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
    from .privacy import redact
    done, failures, streak, seen = [], [], 0, set()
    say = (lambda m: log(redact(m))) if log is not None else (lambda m: None)

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
        except Exception as e:
            err = classify(e)
            info = error_info(e)
            if isinstance(err, BusinessError):
                record(k, err.code, err.message, info)
                streak = 0
                say(f"skip {k}: [{err.code}] {err.message} at {info['where']}")
            elif not err.retryable:
                err.context.setdefault("item", k)
                err.context.setdefault("where", info["where"])
                say(f"stop at {k}: [{err.code}] {err.message} at {info['where']}")
                if err is e:
                    raise
                raise err from e
            else:
                record(k, err.code, err.message, info, retryable=True)
                streak += 1
                say(f"system failure {streak}/{max_consecutive_system} at {k}: [{err.code}] {err.message}")
                if streak >= max_consecutive_system:
                    raise SystemError_(f"{streak} consecutive system failures; last: {info['type']}: {err.message}",
                                       code="SYSTEM_STREAK", last_item=k, where=info["where"]) from e
            continue
        done.append(item); streak = 0
        if checkpoint is not None:
            try:
                checkpoint.mark_done(k)
            except OSError as e:                          # done but not recorded: a re-run would do it again
                raise RobotError(f"{k} was done but the checkpoint could not be written: {type(e).__name__}; fix the "
                                 "folder before re-running", code="CHECKPOINT_FAILED", item=k) from e
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
        self._save()

    def discard(self, key):
        """Forget one key (e.g. a held submit the target now confirms), written the same safe way."""
        if str(key) in self.done:
            self.done.discard(str(key))
            self._save()

    def _save(self):
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
        from .privacy import redact
        log(redact(f"FAIL  {name} ({(time.perf_counter() - t) * 1000:.0f} ms): {classify(e)} at {error_where(e)}"))
        raise


def run_dir(robot, business_date=None, root="work"):
    """work/<robot>/<YYYY-MM-DD>/ — logs, checkpoint and outputs for one run."""
    d = Path(root) / robot / (business_date or date.today().isoformat())
    d.mkdir(parents=True, exist_ok=True)
    return d
