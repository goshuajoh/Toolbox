"""
title: Screen step that checks itself, retries, falls back and gives up
order: 10
when: >-
  Any click, type or select on a desktop app (pywinauto), a Citrix screen (pyautogui) or a web portal
  (toolbank Browser) where the screen can lag, a pop-up can steal focus, or a control can move.
tags: [screens, retry, failure]
needs: [pywinauto, pyautogui, playwright]
blocks: [RETRY_PROFILES, SystemError_, attempt, ctl_click, ctl_text, hotkey, screen_capture]
decisions:
  - "Check the result on the screen, not the click: a click that 'worked' but saved nothing is the failure that matters. The status bar must show the app's save message for this reference (SAVED, e.g. 'Record T1 saved'), as whole words: 'Record T10 saved' or 'Record T1 opened' does not count as T1 saved."
  - "3 tries 2 s apart, each checked for up to 10 s (RETRY_PROFILES['ui']): covers a slow screen and one pop-up, and still fails within about a minute."
  - "Between tries press Esc and re-focus the window (recover), so one stray dialog does not spoil every retry. A recover that fails is logged and the next try still runs."
  - "One fallback route (the keyboard shortcut instead of the button): a different way in beats repeating the same one."
  - "saved() is both the check after each click and already_done before each try, the first too. If the status bar cannot be read (a frozen window), it raises, and attempt stops the step with SystemError_ DONE_CHECK_FAILED instead of clicking Save again: a save that may have landed is never repeated blind. attempt takes no screenshot on that path, so save_record takes one."
  - "The save message must not be on the bar before the first click. If it is (an earlier save of the same record in this session), saved() cannot tell it from a new save: it raises, and the step stops with DONE_CHECK_FAILED. Taking it as done would skip this save and lose the edit without anyone noticing."
  - "Only screen trouble is retried. A bug (TypeError, AttributeError, NameError) is raised at once (exit 5). A KeyError or ValueError is bad data: BusinessError BAD_DATA, the item is skipped and reported."
  - "On giving up: screenshot as evidence, then SystemError_ STEP_FAILED. STEP_FAILED and DONE_CHECK_FAILED are system failures: process_all records the item as retryable, stops after 3 in a row, and the template exits 3 (re-run later)."
governance:
  - "saved() reads this window's status bar, so it guards the tries of this run only. A re-run after a stop clicks Save again on a fresh status bar (fine for an edit to an existing record), or stops with DONE_CHECK_FAILED while the bar still shows the earlier save. A save that creates a record, posts, pays or sends goes through the idempotent_submit pattern, which looks the reference up in the target first."
  - "Screenshots can show customer data: keep them in the run folder, under your data retention rule, never in a shared drive. Their names carry the step, the time and, for a save, the record's reference, never an account number."
  - "attempt's log lines name the step and the reference, never an account number or a field value, and carry the screen's error text, redacted. Pass log=log.warning so they land in run.log.jsonl."
"""
import re
import time

from toolbank import RETRY_PROFILES, SystemError_, attempt, ctl_click, ctl_text, hotkey, screen_capture

SAVED = "Record {ref} saved"                              # TODO: the status bar text your app shows after a save


def save_record(window, ref, run_folder, log=print):
    """Desktop app: click Save, confirm the status bar shows the save message for ref; Ctrl+S as the fallback."""
    shown = re.compile(rf"(?<![\w-]){re.escape(SAVED.format(ref=ref))}(?![\w-])")   # 'T1 saved' is not 'T10 saved'
    reads = []                                            # the first read is attempt's already_done, before any click

    def saved():                                          # True / False; raises when it cannot tell
        found = bool(shown.search(str(ctl_text(window, auto_id="statusBar", timeout=1) or "")))
        if found and not reads:                           # shown before any click: an earlier save, not this one
            raise SystemError_("the save message showed before Save was clicked", code="STALE_STATUS")
        reads.append(found)
        return found

    def recover():
        hotkey("esc")                                     # close whatever pop-up took focus
        window.set_focus()

    def evidence():                                       # a failed screenshot never hides the real error
        name = re.sub(r"[^\w-]", "_", str(ref))
        try:
            screen_capture(run_folder / f"save_failed_{name}_{time.strftime('%Y%m%d-%H%M%S')}.png")
        except Exception as e:
            log(f"save {ref}: screenshot failed ({type(e).__name__})")

    try:
        return attempt(
            lambda: ctl_click(window, title="Save", control_type="Button"),
            verify=saved,
            recover=recover,
            fallbacks=[lambda: hotkey("ctrl", "s")],
            already_done=saved,                           # read before every try: a save that landed is not repeated
            on_give_up=evidence,
            name=f"save {ref}",
            log=log,
            **RETRY_PROFILES["ui"],
        )
    except SystemError_ as e:
        if e.code == "DONE_CHECK_FAILED":                 # frozen window or stale bar: attempt took no screenshot
            evidence()
        raise


def search_account(b, account, log=print):
    """Web portal (b = toolbank Browser): search, confirm the account appears; Enter as the fallback."""
    return attempt(
        lambda: (b.fill("#account", account), b.click("button#search")),
        verify=lambda: b.exists(f"text={account}", ms=500),
        recover=lambda: b.press("#account", "Escape"),
        fallbacks=[lambda: b.press("#account", "Enter")],
        on_give_up=lambda: b.screenshot("search_failed"),     # Browser adds the time; no account number
        name="search account",                                # the log never carries the account number
        log=log,
        **RETRY_PROFILES["ui"],
    )


# --- demo ---


class FakeTime:
    """Sleeping only moves this clock, so the demo's waits take no real time."""
    now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakeApp:
    """Stands in for pywinauto and pyautogui: one window with a Save button and a status bar. A pop-up can catch the
    first click, a broken screen can ignore every save, and the app can hang in the middle of a save."""

    def __init__(self, ref, popup=False, saves=True, hangs=False, status="Ready"):
        self.ref, self.popup, self.saves, self.hangs, self.status = ref, popup, saves, hangs, status
        self.frozen, self.clicks, self.keys, self.shots = False, 0, [], []

    def _save(self):
        if self.frozen or self.popup or not self.saves:
            return                                        # the click lands on the pop-up, or nothing happens
        if self.hangs:
            self.frozen = True                            # did the save land? nobody can tell
            return
        self.status = f"Record {self.ref} saved"

    def ctl_click(self, window, **locator):
        self.clicks += 1
        self._save()

    def ctl_text(self, window, **locator):
        if self.frozen:
            raise RuntimeError("timed out waiting for statusBar")   # pywinauto's TimeoutError is a RuntimeError
        return self.status

    def hotkey(self, *keys):
        self.keys.append("+".join(keys))
        if keys == ("esc",):
            self.popup = False
        elif keys == ("ctrl", "s"):
            self._save()

    def set_focus(self):
        if self.frozen:
            raise RuntimeError("window not responding")

    def screen_capture(self, path):
        self.shots.append(path.name)
        return path

    def patched(self):
        """The block's screen functions pointed at this fake (the block itself is unchanged)."""
        from unittest import mock
        return mock.patch.dict(globals(), ctl_click=self.ctl_click, ctl_text=self.ctl_text, hotkey=self.hotkey,
                               screen_capture=self.screen_capture)


class FakePortal:
    """Stands in for toolbank Browser: the Search button is dead, Enter in the box works."""

    def __init__(self):
        self.typed, self.found, self.shots = "", False, []

    def fill(self, sel, value): self.typed = value
    def click(self, sel): pass
    def press(self, sel, key): self.found = self.found or key == "Enter"
    def exists(self, sel, ms=2000): return self.found and sel == f"text={self.typed}"
    def screenshot(self, label): self.shots.append(label)


def demo(tmp):
    """A pop-up catches the first click: recover, saved on try 2. A screen that never saves: fallback, screenshot,
    STEP_FAILED. A window that hangs after the click: DONE_CHECK_FAILED, Save is not clicked again. An earlier save
    of the same record still on the bar: DONE_CHECK_FAILED, not skipped as done. A dead Search button: the Enter
    fallback finds the account."""
    from functools import partial
    from unittest import mock

    t, lines = FakeTime(), []
    with mock.patch.dict(globals(), attempt=partial(attempt, sleep=t.sleep, clock=t.clock)):
        app = FakeApp("T1", popup=True, status="Record T1 opened")      # 'opened' is not 'saved'
        with app.patched():
            save_record(app, "T1", tmp, log=lines.append)
        assert app.clicks == 2 and app.keys == ["esc"] and app.shots == []
        assert lines[-1] == "save T1: ok on primary try 2"

        app = FakeApp("T2", saves=False, status="Record T20 saved")      # T20 on the bar is not T2
        with app.patched():
            try:
                save_record(app, "T2", tmp, log=lines.append)
            except SystemError_ as e:
                assert e.code == "STEP_FAILED" and len(e.context["history"]) == 4
            else:
                raise AssertionError("expected STEP_FAILED")
        assert app.clicks == 3 and app.keys == ["esc", "esc", "esc", "ctrl+s"]
        assert len(app.shots) == 1 and app.shots[0].startswith("save_failed_T2_")

        app = FakeApp("T3", hangs=True)
        with app.patched():
            try:
                save_record(app, "T3", tmp, log=lines.append)
            except SystemError_ as e:
                assert e.code == "DONE_CHECK_FAILED" and e.context["history"][-1]["outcome"] == "done check failed"
            else:
                raise AssertionError("expected DONE_CHECK_FAILED")
        assert app.clicks == 1 and app.keys == ["esc"] and len(app.shots) == 1    # never clicked Save again
        assert "save T3: recover failed: RuntimeError: window not responding" in lines

        app = FakeApp("T4", status="Record T4 saved")                     # an earlier save of T4 still shows
        with app.patched():
            try:
                save_record(app, "T4", tmp, log=lines.append)
            except SystemError_ as e:
                assert e.code == "DONE_CHECK_FAILED" and "STALE_STATUS" in e.context["history"][-1]["error"]
            else:
                raise AssertionError("expected DONE_CHECK_FAILED")
        assert app.clicks == 0 and app.keys == [] and len(app.shots) == 1         # stopped, not skipped as done

        portal = FakePortal()
        search_account(portal, "12345678", log=lines.append)
        assert lines[-1] == "search account: ok on fallback 1 try 1" and portal.shots == []
    assert not any("12345678" in ln for ln in lines)
