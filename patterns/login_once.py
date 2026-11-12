"""
title: Log in once, safely, and reuse the session
order: 40
when: A robot that signs in to a web portal with a service account, once per run, around the PROCESS part.
tags: [login, credentials, session, web, security]
needs: [playwright, keyring]
blocks: [RETRY_PROFILES, RobotError, SystemError_, attempt, get_secret]
decisions:
  - "One try only (RETRY_PROFILES['login']): every wrong password counts towards the lockout limit (often 3 to 5), and a locked service account stops every robot that uses it."
  - "After submit, wait up to 30 s for the home page or a message. Neither is SystemError_ LOGIN_TIMEOUT (slow page, no form): the template exits 3, safe to re-run later at the cost of one more try."
  - "A message with locked, too many, expired, invalid or incorrect is RobotError AUTH: the run stops (template exit 5) and the owner fixes the account first. An unknown message is AUTH too, since guessing could lock the account: add the portal's words to LOGIN_MESSAGES."
  - "'maintenance' or 'unavailable' on the login page is SystemError_ LOGIN_UNAVAILABLE: the portal is down, re-run later."
  - "Reuse a saved session younger than 8 hours (one working day) only if the home page shows within 5 s; otherwise delete the file and log in."
  - "A missing secret is RobotError NO_SECRET (exit 5, fix the setup), not the KeyError get_secret raises, which classify would report as bad data (exit 2)."
governance:
  - "Credentials come from get_secret (env var, or Windows Credential Manager through keyring) only when a login is needed and are never logged: attempt's log lines go through a password mask, errors carry a reason word, never the screen text (it can name the user)."
  - "The session file is a password: owner-only (chmod 600 on Linux; on Windows the folder's ACL decides), in a folder only the robot account can read, not the run folder with reports, never a shared drive or git."
  - "Logout in finally deletes the session file, so a failed run leaves no live session behind. keep=True (a robot that runs many times a day) is bounded by the 8-hour limit."
"""
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from toolbank import RETRY_PROFILES, RobotError, SystemError_, attempt, get_secret

SEL = {"user": "#username", "password": "#password", "submit": "button[type=submit]",   # change per portal
       "home": "#logout", "message": ".login-error", "logout": "#logout"}       # home: only a logged-in page has it
LOGIN_MESSAGES = [                                        # word on the login page -> kind, reason (first match wins)
    ("locked", "AUTH", "locked"), ("too many", "AUTH", "locked"), ("expired", "AUTH", "expired password"),
    ("invalid", "AUTH", "invalid credentials"), ("incorrect", "AUTH", "invalid credentials"),
    ("maintenance", "SYSTEM", "maintenance"), ("unavailable", "SYSTEM", "unavailable"),
]


def login_state(b):
    """('OK', '') on the home page, (kind, reason) when the login page shows a message, None while it loads."""
    if b.exists(SEL["home"], ms=200):                     # home first: a banner there ("expires in 5 days") is fine
        return "OK", ""
    if not b.exists(SEL["message"], ms=200):              # never ms=0: Playwright reads 0 as "no time limit"
        return None
    text = b.text(SEL["message"]).lower()
    return next(((kind, why) for word, kind, why in LOGIN_MESSAGES if word in text), ("AUTH", "unrecognised message"))


def login(b, user, password, *, log=print, sleep=time.sleep, clock=time.monotonic):
    """One try on the open login page: submit, wait up to 30 s for the home page or a message, act on it."""
    seen = {}

    def answered():
        seen["state"] = login_state(b)
        return seen["state"]

    try:
        attempt(lambda: b.login(user, password, SEL["user"], SEL["password"], SEL["submit"]), verify=answered,
                name="login", log=lambda m: log(m.replace(password, "***")), sleep=sleep, clock=clock,
                **RETRY_PROFILES["login"])
    except SystemError_:
        raise SystemError_("login did not finish (slow page or no form): re-run later", code="LOGIN_TIMEOUT") from None
    kind, reason = seen["state"]
    if kind == "AUTH":
        raise RobotError(f"login refused ({reason}): stop and tell the robot owner", code="AUTH", reason=reason)
    if kind == "SYSTEM":
        raise SystemError_(f"login page says {reason}: re-run later", code="LOGIN_UNAVAILABLE", reason=reason)
    log("login: ok")


@contextmanager
def portal_session(b, url, session_file, user_name, password_name, *, max_age_h=8, keep=False, log=print,
                   sleep=time.sleep, clock=time.monotonic):
    """`with portal_session(b, ...):` reuse a young saved session or log in once; at the end log out, delete it."""
    f = Path(session_file)
    fresh = f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600
    if fresh:
        b.load_session(f)
    b.goto(url)
    if fresh and b.exists(SEL["home"], ms=5000):
        log("login: saved session reused")
    else:
        f.unlink(missing_ok=True)                         # too old or refused by the portal: never reuse it
        try:
            user, password = get_secret(user_name), get_secret(password_name)   # env var or Credential Manager
        except KeyError as e:
            raise RobotError(e.args[0], code="NO_SECRET") from e                 # a setup error, not bad data
        login(b, user, password, log=log, sleep=sleep, clock=clock)
        f.parent.mkdir(parents=True, exist_ok=True)
        b.save_session(f)
        f.chmod(0o600)                                    # owner only on Linux; on Windows the folder's ACL decides
    try:
        yield b
    finally:
        if not keep:
            try:
                b.click(SEL["logout"])
            except Exception as e:                        # page gone or session over: still delete the file
                log(f"logout failed: {type(e).__name__}")
            f.unlink(missing_ok=True)


class FakeTime:
    """For the demo: sleeping only moves this clock, so waits take no real time."""
    now = 0.0
    def clock(self): return self.now
    def sleep(self, s): self.now += s


class FakeBrowser:
    """For the demo: a login page with the Browser methods used above; reply=None: the page never answers."""
    def __init__(self, server, password, reply="Invalid user ID or password"):
        self.server, self.password, self.reply, self.page, self.cookie, self.tries = server, password, reply, "", "", 0
    def goto(self, url): self.page = "home" if self.cookie in self.server else "login"
    def exists(self, sel, ms=2000): return (sel, self.page) in {(SEL["home"], "home"), (SEL["message"], "message")}
    def text(self, sel): return self.reply
    def click(self, sel): self.server.discard(self.cookie); self.page = "login"     # the logout link
    def save_session(self, path): Path(path).write_text(self.cookie)
    def load_session(self, path): self.cookie = Path(path).read_text()

    def login(self, user, password, *selectors):
        self.tries += 1
        if password == self.password: self.cookie, self.page = user, "home"; self.server.add(user)
        elif self.reply: self.page = "message"


def demo(tmp):
    """Wrong password: AUTH after exactly one try. Good password: one login, the next run reuses the session,
    logout deletes the file. A page that never answers: LOGIN_TIMEOUT after one 30 s wait. No password in the log."""
    t, log, server, url = FakeTime(), [], set(), "https://portal.example/"
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    names, session = ("DEMO_PORTAL_USER", "DEMO_PORTAL_PASSWORD"), tmp / "secure" / "portal_session.json"

    def refusal(browser):
        try:
            with portal_session(browser, url, session, *names, **kw): pass
        except RobotError as e:
            return e
        raise AssertionError("expected the login to be refused")
    with mock.patch.dict(os.environ, {names[0]: "robot01", names[1]: "Wr0ng-pass"}):   # restored afterwards
        b = FakeBrowser(server, password="Right-pass")
        e = refusal(b)
        assert e.code == "AUTH" and e.context == {"reason": "invalid credentials"} and b.tries == 1
        os.environ[names[1]] = "Right-pass"
        with portal_session(b, url, session, *names, keep=True, **kw):
            assert b.page == "home" and b.tries == 2 and session.exists()
        nxt = FakeBrowser(server, password="Right-pass")    # the next run: no login
        with portal_session(nxt, url, session, *names, **kw):
            assert nxt.page == "home" and nxt.tries == 0
        assert not session.exists() and not server           # logged out, file deleted
        assert refusal(FakeBrowser(server, password="other", reply=None)).code == "LOGIN_TIMEOUT" and t.now == 30
        assert not any("Right-pass" in m or "Wr0ng-pass" in m for m in log)
