import json
from datetime import datetime, timedelta

import pytest

from toolbank import *


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_single_instance(tmp_path):
    with single_instance("bot one", lock_dir=tmp_path) as p:
        assert p.exists() and "pid=" in p.read_text()
        with pytest.raises(BusinessError) as e:
            with single_instance("bot one", lock_dir=tmp_path):
                pass
        assert e.value.code == "ALREADY_RUNNING"
    with single_instance("bot one", lock_dir=tmp_path):      # free again after release
        pass


def test_poll_until_and_wait_until():
    c = FakeClock()
    state = {"n": 0}

    def ready():
        state["n"] += 1
        return "file.csv" if state["n"] == 3 else None

    assert poll_until(ready, timeout_s=60, every_s=10, sleep=c.sleep, clock=c) == "file.csv"
    assert c.slept == [10, 10]
    with pytest.raises(SystemError_) as e:
        poll_until(lambda: False, timeout_s=25, every_s=10, sleep=c.sleep, clock=c)
    assert e.value.code == "TIMEOUT"
    slept = []
    assert wait_until("09:30", now=lambda: datetime(2026, 3, 6, 9, 0), sleep=slept.append) == 1800
    assert wait_until("08:00", now=lambda: datetime(2026, 3, 6, 9, 0), sleep=slept.append) == 0 and slept == [1800]


def test_watch_folder_ledger(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "a.csv").write_text("x\n1\n")
    (inbox / "bad.csv").write_text("x\nBAD\n")
    (inbox / "flaky.csv").write_text("x\nFLAKY\n")
    (inbox / "~$lock.csv").write_text("temp")
    seen, flaky = [], {"n": 0}

    def handler(p):
        text = p.read_text()
        if "BAD" in text:
            raise BusinessError("bad file", code="BAD")
        if "FLAKY" in text:
            flaky["n"] += 1
            if flaky["n"] == 1:
                raise OSError("share unavailable")
        seen.append(p.name)

    c = FakeClock()
    res = watch_folder(inbox, handler, pattern="*.csv", settle_s=0, sleep=c.sleep, clock=c, log=lambda m: None)
    assert {r["file"].split("/")[-1].split("\\")[-1]: r["status"] for r in res} == \
        {"a.csv": "done", "bad.csv": "failed", "flaky.csv": "error"}
    res2 = watch_folder(inbox, handler, pattern="*.csv", settle_s=0, sleep=c.sleep, clock=c, log=lambda m: None)
    assert [r["status"] for r in res2] == ["done"] and seen == ["a.csv", "flaky.csv"]
    (inbox / "a_copy.csv").write_text("x\n1\n")                     # same content, new name -> skipped
    assert watch_folder(inbox, handler, pattern="*.csv", settle_s=0, sleep=c.sleep, clock=c, log=lambda m: None) == []
    ledger = [json.loads(x) for x in (inbox / ".processed.jsonl").read_text().splitlines()]
    assert sorted(x["status"] for x in ledger) == ["done", "done", "failed"]
    assert new_files(inbox, "*.csv", settle_s=0, retry_failed=True)[0][0].name == "bad.csv"


def test_watch_folder_loops_for_duration(tmp_path):
    c = FakeClock()
    calls = []
    watch_folder(tmp_path, calls.append, run_for_s=60, poll_s=30, settle_s=0, sleep=c.sleep, clock=c)
    assert c.slept == [30, 30]


def test_run_every_aligned_and_errors():
    c = FakeClock(1000.0)
    n = {"i": 0}

    def job():
        n["i"] += 1
        c.t += 5                                    # the job takes 5 s
        if n["i"] == 2:
            raise ValueError("boom")

    out = run_every(job, 300, run_for_s=1000, sleep=c.sleep, clock=c, log=lambda m: None)
    assert [o["ok"] for o in out] == [True, False, True, True]
    assert c.slept == [195, 295, 295]               # runs at 1000, 1200, 1500, 1800 (clock-aligned)
    c2 = FakeClock(1000.0)
    assert len(run_every(lambda: None, 60, max_runs=3, align=False, sleep=c2.sleep, clock=c2)) == 3
    assert c2.slept == [60, 60]
    n["i"] = 1                                      # next call is the failing 2nd one
    with pytest.raises(ValueError):
        run_every(job, 60, max_runs=5, stop_on_error=True, sleep=c.sleep, clock=c, log=lambda m: None)


@pytest.mark.parametrize("expr", ["*/15 * * * *", "30 7 * * MON-FRI", "0 9,17 1-7 * *", "0 0 1 */3 *",
                                  "45 23 L * *".replace("L", "28-31"), "0 12 * JAN,JUL SUN", "5/20 * * * *",
                                  "0 6 15 * 1", "@daily", "@weekly", "0 0 29 2 *", "10-50/10 8-18 * * 1-5"])
def test_cron_next_matches_croniter(expr):
    croniter = pytest.importorskip("croniter").croniter
    start = datetime(2026, 1, 30, 23, 58)
    ours = cron_next(expr, start, n=25)
    it = croniter(expr, start)
    theirs = [it.get_next(datetime) for _ in range(25)]
    assert ours == theirs
    assert all(cron_matches(expr, t) for t in ours)


def test_cron_details():
    assert cron_next("0 9 * * MON", datetime(2026, 3, 6, 10, 0)) == datetime(2026, 3, 9, 9, 0)   # Fri -> Mon
    assert cron_next("0 0 1 1 *", datetime(2026, 6, 1)) == datetime(2027, 1, 1)
    assert cron_parse("0 0 * * 7")["weekdays"] == {0}
    assert not cron_matches("0 9 * * 1-5", datetime(2026, 3, 7, 9, 0))          # Saturday
    for bad in ["* * *", "61 * * * *", "* * * * 8", "5-1 * * * *", "*/0 * * * *"]:
        with pytest.raises(ValueError):
            cron_parse(bad)
    with pytest.raises(ValueError):
        cron_next("0 0 30 2 *")                                                   # 30 Feb never happens
    assert cron_line("0 7 * * 1-5", "python bot.py", "bot.log") == "0 7 * * 1-5 python bot.py >> bot.log 2>&1"
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Asia/Singapore")
    nxt = cron_next("30 8 * * *", datetime(2026, 3, 6, 9, 0, tzinfo=tz))
    assert nxt == datetime(2026, 3, 7, 8, 30, tzinfo=tz) and nxt.tzinfo is tz


def test_run_on_cron_with_fake_time():
    t = {"now": datetime(2026, 3, 6, 8, 59, 30)}
    ran = []

    def sleep(s):
        t["now"] += timedelta(seconds=s)

    out = run_on_cron(lambda: ran.append(t["now"]), "*/30 9-10 * * *", run_for_s=3 * 3600, now=lambda: t["now"],
                      sleep=sleep, log=lambda m: None)
    assert [r.strftime("%H:%M") for r in ran] == ["09:00", "09:30", "10:00", "10:30"]
    assert all(o["ok"] for o in out)


def test_schtasks_commands(tmp_path, capsys):
    argv = schtasks_create("CoE\\Recon", r'"C:\Py\python.exe" "C:\bots\recon.py" --env prod', schedule="weekly",
                           days=["MON", "FRI"], start_time="07:15")
    assert argv[:4] == ["schtasks", "/Create", "/TN", "CoE\\Recon"]
    assert ["/SC", "WEEKLY"] == argv[6:8] and "/IT" in argv and argv[-1] == "/F"
    assert argv[argv.index("/D") + 1] == "MON,FRI" and argv[argv.index("/ST") + 1] == "07:15"
    assert "[dry-run] schtasks /Create" in capsys.readouterr().out
    assert "/ST" not in schtasks_create("x", "y", schedule="ONLOGON")
    assert schtasks_delete("x") == ["schtasks", "/Delete", "/TN", "x", "/F"]
    bat = write_runner_bat(tmp_path / "run.bat", tmp_path / "bot.py", python_exe=r"C:\Py\python.exe",
                           args=["--env", "prod"], log_file=tmp_path / "bot.log")
    lines = bat.read_bytes().decode("ascii").split("\r\n")
    assert lines[0] == "@echo off" and lines[1].startswith('cd /d "') and lines[3] == "exit /b %ERRORLEVEL%"
    assert lines[2].startswith('"C:\\Py\\python.exe" "') and '"--env" "prod" >> "' in lines[2]


def test_webhook_server():
    requests = pytest.importorskip("requests")
    got = []

    def handler(payload, headers):
        if payload.get("bad"):
            raise BusinessError("bad payload", code="BAD_PAYLOAD")
        if payload.get("crash"):
            raise RuntimeError("bug")
        got.append(payload)
        return {"queued": payload["id"]}

    with WebhookServer(handler, secret="k", log=lambda m: None) as hook:
        body = json.dumps({"id": 7}).encode()
        r = requests.post(hook.url, data=body, headers={"X-Signature": sign_body(body, "k")})
        assert r.status_code == 200 and r.json() == {"queued": 7} and got == [{"id": 7}]
        assert requests.post(hook.url, data=body, headers={"X-Signature": "sha256=00"}).status_code == 401
        assert requests.post(hook.url, data=body).status_code == 401
        bad = json.dumps({"bad": 1}).encode()
        r = requests.post(hook.url, data=bad, headers={"X-Signature": sign_body(bad, "k")})
        assert r.status_code == 422 and r.json()["code"] == "BAD_PAYLOAD"
        crash = json.dumps({"crash": 1}).encode()
        assert requests.post(hook.url, data=crash, headers={"X-Signature": sign_body(crash, "k")}).status_code == 500
        nj = b"not json"
        assert requests.post(hook.url, data=nj, headers={"X-Signature": sign_body(nj, "k")}).status_code == 400
        assert requests.get(hook.url.replace("/webhook", "/health")).json() == {"ok": True, "received": 1}
        assert requests.post(hook.url.replace("/webhook", "/other"), data=b"{}").status_code == 404
        assert post_webhook(hook.url, {"id": 8}, secret="k") == {"queued": 8}
    with WebhookServer(lambda p, h: None, token="t0k") as hook:
        assert requests.post(hook.url, json={}, headers={"X-Token": "t0k"}).json() == {"ok": True}
        assert requests.post(hook.url, json={}, headers={"X-Token": "nope"}).status_code == 401


def test_file_queue(tmp_path):
    q = FileQueue(tmp_path / "q")
    a = q.put({"trade": "T1"})
    assert q.put({"trade": "T2"}, item_id="T2") == "T2"
    assert q.put({"trade": "T2 again"}, item_id="T2") is None          # duplicate id ignored
    q.put({"trade": "BAD"}, item_id="T3")
    q.put({"trade": "FLAKY"}, item_id="T4")
    assert q.counts() == {"pending": 4, "working": 0, "done": 0, "failed": 0}
    item = q.get()
    assert item["id"] == a and item["attempts"] == 1 and q.counts()["working"] == 1
    q.done(item, {"ok": True})
    attempts = {"n": 0}

    def handler(payload):
        if payload["trade"] == "BAD":
            raise BusinessError("bad", code="BAD")
        if payload["trade"] == "FLAKY":
            attempts["n"] += 1
            raise OSError("down")
        return "booked"

    assert q.process(handler) == {"done": 1, "failed": 1, "retry": 1}
    assert q.process(handler, max_attempts=2) == {"done": 0, "failed": 1, "retry": 0}
    assert attempts["n"] == 2
    assert q.counts() == {"pending": 0, "working": 0, "done": 2, "failed": 2}
    failed = q.items("failed")
    assert set(failed["id"]) == {"T3", "T4"} and "error" in failed.columns
    q.put({"trade": "T5"}, item_id="T5")
    stuck = q.get()
    import os
    import time
    old = time.time() - 7200
    os.utime(q.root / "working" / f"{stuck['id']}.json", (old, old))
    assert q.requeue_stale(3600) == 1 and q.counts()["pending"] == 1
    assert q.get(item_id="nope") is None
