"""
title: Robot run that always shuts down cleanly
order: 20
when: "Any robot that opens apps (browser, Excel, a desktop or Citrix client): a crash, a bug or an ops STOP still closes them, keeps evidence, tells the owner and exits with the right code."
tags: [failure, controls, notify]
needs: [playwright, pywin32, pywinauto, pyautogui, requests]
blocks: [Browser, BusinessError, Checkpoint, SystemError_, app_connect, classify, error_info, excel_app, feature_flag, get_logger, get_secret, process_all, screen_capture, single_instance, teams_message]
decisions:
  - "One contextlib.ExitStack per run. Each closer is registered the moment its app opens, so if the third app fails to start the first two still close, last opened first. The single_instance lock is entered first, so it is released last: a second copy cannot start while this run's apps are still closing. run_robot takes the lock itself, so it replaces the template's single_instance block in main(); keeping both makes every run exit 6."
  - "Evidence first: the screenshot hook is pushed after the desktop app opens, so on an error or a STOP it runs before the app is killed. Browser takes its own screenshot on an error exit. No screenshot on a clean end. Each run writes under its own time stamp, so a re-run never overwrites the last failure's evidence."
  - "Exit codes as the template: BusinessError 2, retryable (SystemError_, OSError) 3, anything else 5, second copy 6. Item failures give 3 if any is retryable, else 2. No card for 0 or 6. Every closer runs through quietly(): a failed close is logged and swallowed, else ExitStack replaces the real error with the close error and a bug (exit 5) shows up as an OSError (exit 3)."
  - "Kill switch: a file named STOP or STOP.txt (Notepad adds .txt) in the run folder, checked before any app opens and then before each item, never inside one. It raises SystemError_ STOPPED, so it takes the same shutdown path and exits 3: a re-run resumes from the checkpoint, and items that failed before the stop are not in it, so they are tried and reported again. The robot never deletes the file; ops do."
  - "feature_flag('robot_enabled') also stops the robot, but a running process cannot see a changed env var or config, so it stops the next run. Only a graceful stop runs the closers: taskkill /F or a reboot runs none (the OS still frees the lock), so ops use the STOP file, not End task."
governance:
  - "The owner's Teams card carries robot, status, exit code, error code, counts and run folder, never the error message (it can hold a value). It is sent once, not retried, only printed in a dry run, and a failed send never changes the exit code. The webhook URL is a secret (anyone holding it can post): get_secret, never config."
  - "The log line carries code, type, where and the ids in context, not the message or the traceback. process_all gets no log= because its lines repeat error messages; item failures go to report(done, failures), the robot's exceptions report, written before the lock is released. A run that ends early writes none: its failed items are not in the checkpoint, so the re-run reports them."
  - "Kill only an app the robot started (app_connect(exe=...)): an app attached by its title can be a person's session. Screenshots can show customer data: they stay in the run folder under the retention rule."
"""
import contextlib
import time

from toolbank import (Browser, BusinessError, Checkpoint, SystemError_, app_connect, classify, error_info, excel_app,
                      feature_flag, get_logger, get_secret, process_all, screen_capture, single_instance, teams_message)

ROBOT = "my_robot"
STATUS = {2: "BUSINESS EXCEPTIONS", 3: "RE-RUN LATER", 5: "NEEDS A FIX"}          # no card for 0 or 6


def quietly(close, what, log):
    """Run one closer; a failure is logged by name and swallowed, so it never replaces the error being handled."""
    try:
        close()
    except Exception as e:
        log.warning(f"close {what} failed", extra={"code": "CLOSE_FAILED", "type": type(e).__name__})


def enter(stack, cm, what, log):
    """stack.enter_context for an app, with its close run through quietly(); the app cannot swallow the error."""
    value = cm.__enter__()
    stack.push(lambda et, e, tb: quietly(lambda: cm.__exit__(et, e, tb), what, log))
    return value


def on_error(take, log):
    """Exit hook for stack.push: take() evidence on an error only, while the screens are still open."""
    return lambda et, e, tb: e is not None and quietly(take, "evidence", log)      # falsy: the error goes on


def open_apps(stack, rd, log):
    """Open each app and register its closer at once: whatever fails later, they close in reverse order."""
    stamp = time.strftime("%H%M%S")                                        # per run: a re-run keeps old evidence
    b = enter(stack, Browser(rd / "screenshots" / stamp), "browser", log)  # screenshots itself on an error exit
    xl = enter(stack, excel_app(), "excel", log)                           # its own EXCEL.EXE, always quit
    app = app_connect(exe=r"C:\Apps\Core\core.exe")                        # started by the robot, so ours to kill
    stack.callback(quietly, app.kill, "core app", log)
    stack.push(on_error(lambda: screen_capture(rd / f"failed_screen_{stamp}.png"), log))   # first out: app open
    return b, xl, app


def check_stop(rd, cfg=None):
    """Kill switch: SystemError_ STOPPED if ops put a STOP file in the run folder or switched robot_enabled off."""
    if any((rd / n).exists() for n in ("STOP", "STOP.txt")) or not feature_flag("robot_enabled", cfg, default=True):
        raise SystemError_("stopped by the kill switch", code="STOPPED")


def until_stop(items, rd, cfg=None):
    """Yield items, checking the kill switch before each one: a stop lands between two items, never inside one."""
    for item in items:
        check_stop(rd, cfg)
        yield item


def exit_code(exc):
    """Template exit codes: a second copy 6, BusinessError 2, retryable 3, anything else 5."""
    err = classify(exc)
    return 6 if err.code == "ALREADY_RUNNING" else 2 if isinstance(err, BusinessError) else 3 if err.retryable else 5


def notify_owner(facts, log, dry_run=False, post=None):
    """Status-only Teams card, sent once; a failed send is logged and the exit code stands."""
    try:
        url = "" if dry_run else get_secret("TEAMS_WEBHOOK_URL")
        return teams_message(url, f"{ROBOT}: {facts['Status']}", facts, color="D13438", dry_run=dry_run, post=post)
    except Exception as e:
        log.warning("notify failed", extra={"code": "NOTIFY_FAILED", "type": type(e).__name__})


def run_robot(rd, items, handle, report, log, cfg=None, cp=None, key=str, opener=open_apps, notify=notify_owner,
              dry_run=False):
    """In the template's run() after SETUP; drop main()'s single_instance block: one ExitStack, a card -> exit code."""
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(single_instance(ROBOT, lock_dir=rd))         # first in, so released last
            check_stop(rd, cfg)                                              # before any app opens
            apps = opener(stack, rd, log)
            done, failures = process_all(until_stop(items, rd, cfg), lambda item: handle(apps, item),
                                         checkpoint=cp, key=key)             # no log=: it repeats messages
            report(done, failures)                                           # every item failure, on file
        code = 3 if failures["retryable"].any() else 2 if len(failures) else 0
        facts = {"Error code": "", "Done": len(done), "Exceptions": len(failures)}
    except Exception as e:
        info, code = error_info(e), exit_code(e)
        ctx = {k: v for k, v in info["context"].items() if k != "history"}   # ids only: history repeats messages
        level = log.warning if info["code"] in ("ALREADY_RUNNING", "STOPPED") else log.error
        level(f"run ended early [{info['code']}]", extra={"code": info["code"], "type": type(e.__cause__ or e).__name__,
              "where": ctx.pop("where", info["where"]), "context": ctx})    # type: the bug, not process_all's wrapper
        facts = {"Error code": info["code"]}
    if code not in (0, 6):                                                   # apps closed, lock released
        status = "STOPPED" if facts["Error code"] == "STOPPED" else STATUS[code]
        notify({"Robot": ROBOT, "Status": status, "Exit code": code, **facts, "Run folder": str(rd)}, log, dry_run)
    return code


def demo(tmp):
    """A bug while a closer also fails, an ops STOP, the re-run with a business exception, a clean end, a 2nd copy."""
    closed, handled, cards, reports, shut = [], [], [], [], ["evidence", "core", "excel", "browser"]
    fail = {"T2": BusinessError("over limit for Tan Ah Kow", code="OVER_LIMIT"), "T3": TypeError("98,765.43 + None")}
    cp, log = Checkpoint(tmp / "checkpoint.json"), get_logger(tmp / "run.log.jsonl", name="shutdown", console=False)

    def opener(stack, rd, log):                                          # open_apps with fakes that record closing
        for name in ("browser", "excel", "core"):
            enter(stack, contextlib.ExitStack(), name, log).callback(closed.append, name)
        stack.callback(quietly, (rd / "core.pid").unlink, "core pid file", log)   # a closer that fails (no file)
        stack.push(on_error(lambda: closed.append("evidence"), log))

    def handle(apps, item):
        if item in fail:
            raise fail[item]
        handled.append(item)
        if item == "T3":
            (tmp / "STOP.txt").touch()                                   # ops save STOP from Notepad while T3 runs

    def run():
        closed.clear()
        return run_robot(tmp, ["T1", "T2", "T3", "T4"], handle, lambda d, f: reports.append(list(f["item"])), log,
                         cp=cp, opener=opener, dry_run=True, notify=lambda *a: cards.append(str(notify_owner(*a))))

    assert run() == 5 and closed == shut and handled == ["T1"] and "NEEDS A FIX" in cards[0]   # not 3: failed close
    del fail["T3"]                                                       # bug fixed; ops stop the re-run after T3
    assert run() == 3 and closed == shut and handled == ["T1", "T3"] and "STOPPED" in cards[1] and reports == []
    (tmp / "STOP.txt").unlink()                                          # ops remove it: T2 fails again, T4 runs
    assert run() == 2 and closed == shut[1:] and handled[-1] == "T4" and reports == [["T2"]] and "BUSINESS" in cards[2]
    fail.clear()                                                         # the owner fixed T2: a clean end, no card
    assert run() == 0 and handled[-1] == "T2" and reports[-1] == [] and len(cards) == 3
    with single_instance(ROBOT, lock_dir=tmp):                           # the lock is free again; hold it
        assert run() == 6 and closed == [] and len(cards) == 3           # a second copy opens and sends nothing
    assert [exit_code(x) for x in (BusinessError("x"), OSError("x"), TypeError("x"))] == [2, 3, 5]
    text = (tmp / "run.log.jsonl").read_text(encoding="utf-8")
    assert "CLOSE_FAILED" in text and '"TypeError"' in text and '"STOPPED"' in text
    assert not any(s in t for t in [text, *cards] for s in ("Tan Ah Kow", "98,765"))   # ids, never data
