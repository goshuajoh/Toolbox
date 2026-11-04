"""Triggers and scheduling: one-copy-at-a-time lock, folder watch with a processed ledger, run every N minutes,
cron expressions (parse / next run / loop), Windows Task Scheduler, a local webhook receiver, a folder-based
work queue for handing items between robots, and poll-until-ready."""
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from .files import file_sha256, is_locked
from .robust import BusinessError, RobotError, SystemError_, classify


# ---------- one copy at a time ----------

def _lock(fd):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd):
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def single_instance(name, lock_dir=None):
    """`with single_instance("recon_bot"):` stops a second copy running (scheduler fired again while the last
    run is still going). OS lock, freed automatically even if the robot crashes. Raises BusinessError ALREADY_RUNNING."""
    lock_dir = Path(lock_dir or tempfile.gettempdir())
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / (re.sub(r"[^\w.-]+", "_", name) + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock(fd)
    except OSError:
        os.close(fd)
        raise BusinessError(f"{name} is already running (lock {path})", code="ALREADY_RUNNING") from None
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"pid={os.getpid()} started={datetime.now():%Y-%m-%d %H:%M:%S}\n".encode())
        yield path
    finally:
        _unlock(fd)
        os.close(fd)        # the file stays: deleting it would let two copies race


# ---------- poll / wait ----------

def poll_until(check, timeout_s=600, every_s=10, what="condition", sleep=time.sleep, clock=time.monotonic):
    """Call check() every every_s until it returns something truthy (returned), else SystemError_ TIMEOUT.
    E.g. wait for an upstream file/report, a status flag in a table, an API job to finish."""
    deadline = clock() + timeout_s
    while True:
        result = check()
        if result:
            return result
        if clock() >= deadline:
            raise SystemError_(f"{what} not met within {timeout_s}s", code="TIMEOUT")
        sleep(max(0.0, min(every_s, deadline - clock())))


def wait_until(hhmm, now=datetime.now, sleep=time.sleep):
    """Sleep until today's hh:mm (local time); returns at once if already past. Returns seconds waited."""
    h, m = (int(x) for x in hhmm.split(":"))
    t = now()
    target = t.replace(hour=h, minute=m, second=0, microsecond=0)
    wait = (target - t).total_seconds()
    if wait > 0:
        sleep(wait)
    return max(0.0, wait)


# ---------- folder trigger ----------

def _ledger_load(path):
    seen = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                seen[rec["sha256"]] = rec["status"]
            except (ValueError, KeyError):
                continue
    return seen


def _ledger_add(path, file, sha, status, note=""):
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "file": Path(file).name, "sha256": sha,
           "status": status, "note": str(note)[:300]}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def new_files(folder, pattern="*", ledger=None, settle_s=2, retry_failed=False, sleep=time.sleep):
    """One scan: files matching pattern that are complete (size steady for settle_s, not locked) and not yet in
    the ledger (by SHA-256, so a renamed copy is skipped too). Oldest first. Returns [(path, sha256)]."""
    folder = Path(folder)
    ledger = Path(ledger) if ledger else folder / ".processed.jsonl"
    cands = [p for p in folder.glob(pattern) if p.is_file() and not p.name.startswith((".", "~$"))]
    sizes = {p: (p.stat().st_size, p.stat().st_mtime) for p in cands}
    if cands and settle_s:
        sleep(settle_s)
    seen, out = _ledger_load(ledger), []
    for p in sorted(cands, key=lambda q: sizes[q][1]):
        try:
            if (p.stat().st_size, p.stat().st_mtime) != sizes[p] or sizes[p][0] == 0 or is_locked(p):
                continue
            sha = file_sha256(p)
        except OSError:
            continue
        status = seen.get(sha)
        if status == "done" or (status == "failed" and not retry_failed):
            continue
        out.append((p, sha))
    return out


def watch_folder(folder, handler, pattern="*", ledger=None, run_for_s=0, poll_s=30, settle_s=2, retry_failed=False,
                 sleep=time.sleep, clock=time.monotonic, log=print):
    """File trigger: handler(path) for each new complete file. Ledger (default <folder>/.processed.jsonl) records
    done/failed so a restart never redoes a file. BusinessError -> 'failed' (not retried); other errors are
    retried on the next scan. run_for_s=0 = one scan (best under Task Scheduler). Returns [{'file','status','error'}]."""
    folder = Path(folder)
    ledger = Path(ledger) if ledger else folder / ".processed.jsonl"
    start, results, tried = clock(), [], set()
    while True:
        for p, sha in new_files(folder, pattern, ledger, settle_s, retry_failed, sleep):
            if sha in tried:
                continue
            tried.add(sha)
            try:
                handler(p)
                _ledger_add(ledger, p, sha, "done")
                results.append({"file": str(p), "status": "done", "error": ""})
                log(f"done {p.name}")
            except Exception as e:
                err = classify(e)
                if isinstance(err, BusinessError):
                    _ledger_add(ledger, p, sha, "failed", err)
                    results.append({"file": str(p), "status": "failed", "error": str(err)})
                else:
                    tried.discard(sha)
                    results.append({"file": str(p), "status": "error", "error": str(err)})
                log(f"{results[-1]['status']} {p.name}: {err}")
        if clock() - start + poll_s > run_for_s:
            return results
        sleep(poll_s)


# ---------- timed loops ----------

def run_every(fn, every_s, run_for_s=None, max_runs=None, align=True, stop_on_error=False,
              sleep=time.sleep, clock=time.time, log=print):
    """Call fn() now and then every every_s seconds (align=True: on clock boundaries, e.g. :00/:15/:30/:45) until
    run_for_s or max_runs. Overrun -> missed slots are skipped. Errors are logged, loop continues unless
    stop_on_error. Returns [{'run','started','ok','error'}]."""
    start, runs, out = clock(), 0, []
    while True:
        t0 = clock()
        runs += 1
        try:
            fn()
            out.append({"run": runs, "started": datetime.fromtimestamp(t0), "ok": True, "error": ""})
        except Exception as e:
            out.append({"run": runs, "started": datetime.fromtimestamp(t0), "ok": False,
                        "error": f"{type(e).__name__}: {e}"})
            log(f"run {runs} failed: {type(e).__name__}: {e}")
            if stop_on_error:
                raise
        if max_runs and runs >= max_runs:
            return out
        now = clock()
        base = 0 if align else start
        nxt = base + (int((now - base) // every_s) + 1) * every_s
        if run_for_s is not None and nxt - start > run_for_s:
            return out
        sleep(max(0.0, nxt - now))


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}
_DOWS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
_MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
           "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}


def _cron_field(text, lo, hi, names=None):
    def num(x):
        x = x.strip().lower()
        v = names[x[:3]] if names and x[:3] in names else int(x)
        if not lo <= v <= hi:
            raise ValueError(f"{x} out of range {lo}-{hi}")
        return v
    vals = set()
    for part in text.split(","):
        rng, _, step = part.partition("/")
        step = int(step) if step else 1
        if step < 1:
            raise ValueError(f"bad step in {part!r}")
        if rng in ("*", "?"):
            a, b = lo, hi
        elif "-" in rng:
            a, b = (num(x) for x in rng.split("-", 1))
        else:
            a = num(rng)
            b = hi if step > 1 else a          # '5/15' = from 5, every 15
        if a > b:
            raise ValueError(f"range {part!r} runs backwards")
        vals.update(range(a, b + 1, step))
    return vals


def cron_parse(expr):
    """'30 7 * * MON-FRI' -> dict of allowed minutes/hours/days/months/weekdays (0 = Sunday). Also @daily etc."""
    expr = _MACROS.get(expr.strip().lower(), expr)
    f = expr.split()
    if len(f) != 5:
        raise ValueError(f"cron needs 5 fields (minute hour day month weekday), got {expr!r}")
    dows = {0 if d == 7 else d for d in _cron_field(f[4], 0, 7, _DOWS)}
    return {"minutes": _cron_field(f[0], 0, 59), "hours": _cron_field(f[1], 0, 23),
            "days": _cron_field(f[2], 1, 31), "months": _cron_field(f[3], 1, 12, _MONTHS), "weekdays": dows,
            "day_star": f[2].startswith(("*", "?")), "weekday_star": f[4].startswith(("*", "?"))}


def _day_ok(c, t):
    dom, dow = t.day in c["days"], (t.isoweekday() % 7) in c["weekdays"]
    return dom and dow if c["day_star"] or c["weekday_star"] else dom or dow   # standard cron: day OR weekday


def cron_matches(expr, when):
    """True if the cron expression fires at this minute."""
    c = cron_parse(expr) if isinstance(expr, str) else expr
    return (when.minute in c["minutes"] and when.hour in c["hours"] and when.month in c["months"]
            and _day_ok(c, when))


def cron_next(expr, after=None, n=1):
    """Next run time after `after` (default now), local wall-clock; n > 1 -> list of the next n."""
    c = cron_parse(expr)
    after = after or datetime.now()
    tz = after.tzinfo
    t = after.replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=1)
    out, limit = [], t + timedelta(days=366 * 30)
    while len(out) < n:
        if t > limit:
            raise ValueError(f"{expr!r} never fires (checked 30 years ahead)")
        if t.month not in c["months"]:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
        elif not _day_ok(c, t):
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
        elif t.hour not in c["hours"]:
            t = (t + timedelta(hours=1)).replace(minute=0)
        elif t.minute not in c["minutes"]:
            t += timedelta(minutes=1)
        else:
            out.append(t.replace(tzinfo=tz) if tz else t)
            limit = t + timedelta(days=366 * 30)
            t += timedelta(minutes=1)
    return out[0] if n == 1 else out


def run_on_cron(fn, expr, run_for_s=None, max_runs=None, stop_on_error=False, now=datetime.now, sleep=time.sleep,
                log=print):
    """In-process scheduler: sleep until each cron time, call fn(). Errors logged, loop continues unless
    stop_on_error. For production prefer Task Scheduler + single_instance. Returns [{'run','due','ok','error'}]."""
    start, runs, out = now(), 0, []
    while True:
        due = cron_next(expr, now())
        if run_for_s is not None and (due - start).total_seconds() > run_for_s:
            return out
        wait = (due - now()).total_seconds()
        if wait > 0:
            sleep(wait)
        runs += 1
        try:
            fn()
            out.append({"run": runs, "due": due, "ok": True, "error": ""})
        except Exception as e:
            out.append({"run": runs, "due": due, "ok": False, "error": f"{type(e).__name__}: {e}"})
            log(f"cron run {runs} failed: {type(e).__name__}: {e}")
            if stop_on_error:
                raise
        if max_runs and runs >= max_runs:
            return out


def cron_line(expr, command, log_file=None):
    """crontab line for a Linux server (validates the expression)."""
    cron_parse(expr)
    return f"{expr} {command}" + (f" >> {log_file} 2>&1" if log_file else "")


# ---------- Windows Task Scheduler ----------

def _schtasks(argv, dry_run):
    if dry_run:
        print("[dry-run] " + subprocess.list2cmdline(argv))
        return argv
    r = subprocess.run(argv, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise RobotError(f"schtasks failed: {(r.stderr or r.stdout).strip()[:300]}", code="SCHTASKS")
    return r.stdout


def schtasks_create(name, command, schedule="DAILY", start_time="07:30", days=None, every=None, start_date=None,
                    user=None, interactive=True, highest=False, force=True, dry_run=True):
    """Windows Task Scheduler entry. schedule: MINUTE | HOURLY | DAILY | WEEKLY | MONTHLY | ONCE | ONLOGON;
    days='MON,TUE,WED,THU,FRI' (WEEKLY); every=n -> /MO (every 15 with MINUTE). interactive=True (/IT) runs only
    while you are logged on (needed for Excel/Outlook COM). dry_run=True prints the command; False creates it."""
    sc = schedule.upper()
    argv = ["schtasks", "/Create", "/TN", name, "/TR", command, "/SC", sc]
    if every:
        argv += ["/MO", str(every)]
    if days:
        argv += ["/D", days if isinstance(days, str) else ",".join(days)]
    if start_time and sc not in ("ONLOGON", "ONSTART", "ONIDLE"):
        argv += ["/ST", start_time]
    if start_date:
        argv += ["/SD", start_date]          # in the PC's date format
    if user:
        argv += ["/RU", user]
    if interactive:
        argv += ["/IT"]
    if highest:
        argv += ["/RL", "HIGHEST"]
    if force:
        argv += ["/F"]
    return _schtasks(argv, dry_run)


def schtasks_delete(name, dry_run=True):
    """Remove a scheduled task."""
    return _schtasks(["schtasks", "/Delete", "/TN", name, "/F"], dry_run)


def schtasks_run(name, dry_run=False):
    """Start a scheduled task now."""
    return _schtasks(["schtasks", "/Run", "/TN", name], dry_run)


def schtasks_query(name=None):
    """Scheduled tasks (all, or one) -> DataFrame with next/last run time, last result, status."""
    import io
    argv = ["schtasks", "/Query", "/FO", "CSV", "/V"] + (["/TN", name] if name else [])
    df = pd.read_csv(io.StringIO(_schtasks(argv, dry_run=False)), dtype=str)
    return df[df.iloc[:, 0] != df.columns[0]].reset_index(drop=True)   # header repeats per folder


def write_runner_bat(path, script, python_exe=None, args=(), workdir=None, log_file=None):
    """.bat for Task Scheduler: cd to the robot folder, run python, append output to a log, pass the exit code on."""
    path, script = Path(path), Path(script)
    workdir = Path(workdir or script.parent)
    cmd = f'"{python_exe or sys.executable}" "{script}"' + "".join(f' "{a}"' for a in args)
    if log_file:
        cmd += f' >> "{log_file}" 2>&1'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(["@echo off", f'cd /d "{workdir}"', cmd, "exit /b %ERRORLEVEL%", ""]),
                    encoding="ascii", newline="")
    return path


# ---------- local webhook receiver ----------

class WebhookServer:
    """HTTP trigger: POST JSON to http://127.0.0.1:<port>/webhook -> handler(payload, headers) -> JSON reply.
    secret= checks the X-Signature HMAC (api.sign_body); token= checks an X-Token header. GET /health -> ok.
    Keep handler fast (e.g. FileQueue.put and return). `with WebhookServer(fn, port=8085, secret=s) as hook:`."""

    def __init__(self, handler, host="127.0.0.1", port=0, path="/webhook", secret=None, token=None,
                 signature_header="X-Signature", max_bytes=1_000_000, log=print):
        self.handler, self.host, self.port, self.path = handler, host, port, path
        self.secret, self.token, self.sig_header = secret, token, signature_header
        self.max_bytes, self.log = max_bytes, log
        self.received, self._srv, self._thread = 0, None, None

    def _make_handler(self):
        outer = self
        from .api import verify_signature

        class _H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _reply(self, status, obj):
                body = json.dumps(obj, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlsplit(self.path).path.rstrip("/") == "/health":
                    return self._reply(200, {"ok": True, "received": outer.received})
                self._reply(404, {"error": "not found"})

            def do_POST(self):
                if urlsplit(self.path).path != outer.path:
                    return self._reply(404, {"error": "not found"})
                if self.headers.get("Content-Length") is None:
                    return self._reply(411, {"error": "Content-Length required"})
                n = int(self.headers.get("Content-Length") or 0)
                if n > outer.max_bytes:
                    return self._reply(413, {"error": "body too large"})
                body = self.rfile.read(n)
                if outer.secret and not verify_signature(body, outer.secret, self.headers.get(outer.sig_header)):
                    return self._reply(401, {"error": "bad signature"})
                if outer.token and not hmac.compare_digest(self.headers.get("X-Token", "").encode(),
                                                           str(outer.token).encode()):
                    return self._reply(401, {"error": "bad token"})
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    return self._reply(400, {"error": "body is not JSON"})
                try:
                    result = outer.handler(payload, dict(self.headers))
                except BusinessError as e:
                    return self._reply(422, {"error": e.message, "code": e.code})
                except Exception as e:
                    outer.log(f"webhook handler failed: {type(e).__name__}: {e}")
                    return self._reply(500, {"error": type(e).__name__})
                outer.received += 1
                self._reply(200, result if result is not None else {"ok": True})
        return _H

    def start(self):
        """Serve in a background thread; returns self (.url is the address)."""
        self._srv = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self):
        """Serve in the foreground until Ctrl+C."""
        self.start()
        try:
            while self._thread.is_alive():
                self._thread.join(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}{self.path}"

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


# ---------- folder work queue ----------

class FileQueue:
    """Work queue in plain folders (pending / working / done / failed) to hand items between robots or retry
    later. Claiming is an atomic rename, so two workers never take the same item. Item = JSON-able dict."""

    FOLDERS = ("pending", "working", "done", "failed")

    def __init__(self, root):
        self.root = Path(root)
        for d in self.FOLDERS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def _file(self, folder, item_id):
        return self.root / folder / f"{item_id}.json"

    @staticmethod
    def _write(path, item):
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(json.dumps(item, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def put(self, payload, item_id=None):
        """Add an item; returns its id. A given item_id already pending/working/done is not added again (None)."""
        if item_id is not None:
            item_id = re.sub(r"[^\w.-]+", "_", str(item_id))
            if any(self._file(f, item_id).exists() for f in ("pending", "working", "done")):
                return None
        item_id = item_id or f"{datetime.now():%Y%m%d%H%M%S%f}-{uuid.uuid4().hex[:8]}"
        self._write(self._file("pending", item_id), {"id": item_id, "payload": payload, "attempts": 0,
                                                      "added": datetime.now().isoformat(timespec="seconds")})
        return item_id

    def pending_ids(self):
        """Ids waiting in pending, oldest first."""
        def age(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return float("inf")
        return [p.stem for p in sorted((self.root / "pending").glob("*.json"), key=lambda q: (age(q), q.name))]

    def get(self, item_id=None):
        """Claim the oldest pending item (or that item_id) -> dict(id, payload, attempts, ...); None if none left."""
        for i in ([item_id] if item_id else self.pending_ids()):
            p, dest = self._file("pending", i), self._file("working", i)
            try:
                os.rename(p, dest)
            except OSError:
                continue                                    # another worker took it
            item = json.loads(dest.read_text(encoding="utf-8"))
            item["attempts"] = item.get("attempts", 0) + 1
            item["claimed"] = datetime.now().isoformat(timespec="seconds")
            self._write(dest, item)
            return item
        return None

    def done(self, item, result=None):
        """Mark a claimed item finished."""
        src = self._file("working", item["id"])
        item = {**item, "result": result, "finished": datetime.now().isoformat(timespec="seconds")}
        self._write(src, item)
        os.replace(src, self._file("done", item["id"]))

    def fail(self, item, error, retry=False, max_attempts=3):
        """Failed item: back to pending if retry and attempts < max_attempts, else to failed."""
        src = self._file("working", item["id"])
        item = {**item, "error": str(error)[:500]}
        self._write(src, item)
        back = retry and item.get("attempts", 0) < max_attempts
        os.replace(src, self._file("pending" if back else "failed", item["id"]))
        return "pending" if back else "failed"

    def requeue_stale(self, older_than_s=3600):
        """Items stuck in working (worker crashed) longer than older_than_s go back to pending; returns count."""
        n, cutoff = 0, time.time() - older_than_s
        for p in (self.root / "working").glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    os.replace(p, self.root / "pending" / p.name)
                    n += 1
            except OSError:
                continue
        return n

    def counts(self):
        """{'pending': n, 'working': n, 'done': n, 'failed': n}."""
        return {f: len(list((self.root / f).glob("*.json"))) for f in self.FOLDERS}

    def items(self, folder="failed"):
        """Items in a folder as a DataFrame (id, attempts, error, payload...)."""
        rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.root / folder).glob("*.json"))]
        return pd.json_normalize(rows) if rows else pd.DataFrame(columns=["id"])

    def process(self, handler, max_items=None, max_attempts=3):
        """Work the items pending now: handler(payload) -> result. BusinessError -> failed; other errors go back
        to pending for the next run (up to max_attempts). Returns {'done': n, 'failed': n, 'retry': n}."""
        out = {"done": 0, "failed": 0, "retry": 0}
        for item_id in self.pending_ids()[:max_items]:
            item = self.get(item_id)
            if item is None:
                continue                                    # another worker took it
            try:
                self.done(item, handler(item["payload"]))
                out["done"] += 1
            except Exception as e:
                err = classify(e)
                where = self.fail(item, err, retry=not isinstance(err, BusinessError), max_attempts=max_attempts)
                out["retry" if where == "pending" else "failed"] += 1
        return out
