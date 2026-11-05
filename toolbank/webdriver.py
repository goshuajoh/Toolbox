"""Selenium WebDriver (Edge or Chrome) for teams that standardise on Selenium; Playwright (web.py) is usually
simpler. pip install selenium. If Selenium Manager cannot download a driver at work, set driver_path to the
msedgedriver.exe from your software store (same major version as Edge)."""
import time
from contextlib import contextmanager
from pathlib import Path

from .robust import SystemError_


def webdriver_start(browser="edge", headless=True, download_dir=None, driver_path=None, binary=None, args=(),
                    page_load_timeout=60):
    """Start Edge (default) or Chrome; download_dir = where downloads land without a prompt."""
    from selenium import webdriver
    if browser == "edge":
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service
        cls = webdriver.Edge
    else:
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        cls = webdriver.Chrome
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    for a in ("--window-size=1600,1000", *args):
        opts.add_argument(a)
    if binary:
        opts.binary_location = str(binary)
    if download_dir:
        d = Path(download_dir).resolve()
        d.mkdir(parents=True, exist_ok=True)
        opts.add_experimental_option("prefs", {"download.default_directory": str(d),
                                               "download.prompt_for_download": False,
                                               "download.directory_upgrade": True})
    driver = cls(options=opts, service=Service(executable_path=str(driver_path)) if driver_path else Service())
    driver.set_page_load_timeout(page_load_timeout)
    return driver


@contextmanager
def webdriver_session(screenshots_dir="screenshots", **start_kw):
    """`with webdriver_session(browser="edge") as d:` — screenshot on error, browser always closed."""
    d = webdriver_start(**start_kw)
    try:
        yield d
    except Exception:
        wd_screenshot(d, Path(screenshots_dir) / f"error_{time.strftime('%Y%m%d_%H%M%S')}.png")
        raise
    finally:
        d.quit()


def _wait(d, css, timeout, state):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    cond = {"visible": EC.visibility_of_element_located, "clickable": EC.element_to_be_clickable,
            "present": EC.presence_of_element_located}[state]
    try:
        return WebDriverWait(d, timeout).until(cond((By.CSS_SELECTOR, css)))
    except Exception as e:
        raise SystemError_(f"element {css!r} not {state} after {timeout}s", code="WEB_ACTION", url=d.current_url) from e


def wd_wait(d, css, timeout=20, state="visible"):
    """Wait for a CSS selector (state: visible | clickable | present) -> element."""
    return _wait(d, css, timeout, state)


def wd_click(d, css, timeout=20):
    """Wait until clickable, click (not retried: it may have submitted)."""
    _wait(d, css, timeout, "clickable").click()


def wd_fill(d, css, value, timeout=20):
    """Clear an input and type value."""
    from .web import _text
    el = _wait(d, css, timeout, "visible")
    el.clear()
    el.send_keys(_text(value))                       # blank (None / NaN) types nothing, not 'nan'


def wd_text(d, css, timeout=20):
    """Visible text of an element."""
    return _wait(d, css, timeout, "visible").text


def wd_select(d, css, value, timeout=20):
    """Choose a <select> option by value, else by visible text."""
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.support.ui import Select
    sel = Select(_wait(d, css, timeout, "present"))
    try:
        sel.select_by_value(str(value))
    except NoSuchElementException:
        sel.select_by_visible_text(str(value))


def wd_js(d, script, *args):
    """Run JavaScript (function body, use `return`): wd_js(d, "return document.title")."""
    return d.execute_script(script, *args)


def wd_tables(d, match=None):
    """Every <table> on the page -> list of DataFrames."""
    from .htmlparse import html_tables
    return html_tables(d.page_source, match=match)


def wd_accept_alert(d, accept=True, timeout=5):
    """Answer an alert/confirm box; returns its message."""
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    WebDriverWait(d, timeout).until(EC.alert_is_present())
    alert = d.switch_to.alert
    msg = alert.text
    alert.accept() if accept else alert.dismiss()
    return msg


def wd_frame(d, css=None, timeout=20):
    """Switch into an iframe by CSS selector; css=None switches back to the main page."""
    if css is None:
        d.switch_to.default_content()
    else:
        d.switch_to.frame(_wait(d, css, timeout, "present"))


def wd_window(d, index=-1):
    """Switch to a tab/window (-1 = newest)."""
    d.switch_to.window(d.window_handles[index])


def wd_wait_download(folder, timeout_s=120, pattern="*", since=None):
    """Wait for a finished download in folder (no .crdownload / .tmp / hidden temp file left) -> newest Path."""
    folder, since = Path(folder), since if since is not None else time.time() - 1
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        files = []
        for p in folder.glob(pattern):
            try:
                if p.is_file():
                    st = p.stat()
                    files.append((p, st.st_mtime, st.st_size))
            except OSError:
                continue                                   # renamed while we looked
        temp = {p for p, _, _ in files if p.suffix.lower() in (".crdownload", ".tmp", ".partial", ".part")
                or p.name.startswith(".")}                 # Chrome on Linux writes .org.chromium.* first
        busy = [p for p, m, _ in files if p in temp and m >= since]   # old leftovers do not block
        done = [(p, m) for p, m, size in files if p not in temp and m >= since and size > 0]
        if done and not busy:
            return max(done, key=lambda x: x[1])[0]
        time.sleep(0.5)
    raise SystemError_(f"no finished download in {folder} after {timeout_s}s", code="DOWNLOAD_TIMEOUT")


def wd_requests_session(d, **session_kw):
    """requests.Session with the browser's cookies + user agent (log in once, then call APIs with requests)."""
    from .api import http_session
    s = http_session(**session_kw)
    for c in d.get_cookies():
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    s.headers["User-Agent"] = d.execute_script("return navigator.userAgent")
    return s


def wd_screenshot(d, path):
    """Save a PNG of the current window; returns the path (None if it failed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        return p if d.save_screenshot(str(p)) else None
    except Exception:
        return None
