"""
title: Screen step that checks itself, retries, falls back and gives up
order: 10
when: >-
  Any click, type or select on a desktop app (pywinauto), a Citrix screen (pyautogui) or a web portal
  (toolbank Browser) where the screen can lag, a pop-up can steal focus, or a control can move.
tags: [screens, retry, failure]
needs: [pywinauto, pyautogui]
blocks: [RETRY_PROFILES, SystemError_, attempt, ctl_click, ctl_text, hotkey, screen_capture]
decisions:
  - "Check the result on the screen, not the click: a click that 'worked' but saved nothing is the failure that matters."
  - "3 tries 2 s apart, each checked for up to 10 s (RETRY_PROFILES['ui']): covers a slow screen and one pop-up, and still fails within about a minute."
  - "Between tries press Esc and re-focus the window (recover), so one stray dialog does not spoil every retry."
  - "One fallback route (the keyboard shortcut instead of the button): a different way in beats repeating the same one."
  - "On giving up: screenshot as evidence, then SystemError_ STEP_FAILED. process_all stops after 3 such failures in a row and the template exits 3 (re-run later)."
governance:
  - "already_done checks the record before every try, so a slow save is never posted twice."
  - "Screenshots can show customer data: keep them in the run folder, under your data retention rule, never in a shared drive."
  - "The log line for each failed try names the step and the reference only, never field values."
"""
from toolbank import RETRY_PROFILES, SystemError_, attempt, ctl_click, ctl_text, hotkey, screen_capture


def save_record(window, ref, run_folder):
    """Desktop app: click Save, confirm the status bar shows the reference; Ctrl+S as the fallback."""
    def saved():
        return ref in (ctl_text(window, auto_id="statusBar", timeout=1) or "")

    def recover():
        hotkey("esc")                                     # close whatever pop-up took focus
        window.set_focus()

    return attempt(
        lambda: ctl_click(window, title="Save", control_type="Button"),
        verify=saved,
        recover=recover,
        fallbacks=[lambda: hotkey("ctrl", "s")],
        already_done=saved,                               # never save twice
        on_give_up=lambda: screen_capture(run_folder / f"save_failed_{ref}.png"),
        name=f"save {ref}",
        **RETRY_PROFILES["ui"],
    )


def search_account(b, account):
    """Web portal (b = toolbank Browser): search, confirm the account appears; Enter as the fallback."""
    return attempt(
        lambda: (b.fill("#account", account), b.click("button#search")),
        verify=lambda: b.exists(f"text={account}", ms=500),
        recover=lambda: b.press("#account", "Escape"),
        fallbacks=[lambda: b.press("#account", "Enter")],
        on_give_up=lambda: b.screenshot(f"search_failed_{account}"),
        name=f"search {account}",
        **RETRY_PROFILES["ui"],
    )


class FakeTime:
    """For the demo and tests: sleeping only moves this clock, so waits take no real time."""
    now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


def demo(tmp):
    """A fake screen that shows a pop-up on the first click: the step recovers and succeeds on try 2.
    A screen that never responds: the step gives up with evidence and STEP_FAILED."""
    t = FakeTime()
    screen = {"popup": True, "saved": False, "clicks": 0}

    def click_save():
        screen["clicks"] += 1
        if screen["popup"]:
            return                                        # the click lands on the pop-up, nothing is saved
        screen["saved"] = True

    def close_popup():
        screen["popup"] = False

    log = []
    attempt(click_save, verify=lambda: screen["saved"], recover=close_popup, already_done=lambda: screen["saved"],
            name="save T1", log=log.append, sleep=t.sleep, clock=t.clock, **RETRY_PROFILES["ui"])
    assert screen == {"popup": False, "saved": True, "clicks": 2}
    assert log[-1] == "save T1: ok on primary try 2"

    evidence = []
    try:
        attempt(lambda: None, verify=lambda: False, fallbacks=[lambda: None],
                on_give_up=lambda: evidence.append(tmp / "save_failed_T2.png"),
                name="save T2", log=log.append, sleep=t.sleep, clock=t.clock, **RETRY_PROFILES["ui"])
    except SystemError_ as e:
        assert e.code == "STEP_FAILED" and len(e.context["history"]) == 4 and evidence
    else:
        raise AssertionError("expected STEP_FAILED")
