"""Web automation with Playwright: pip install playwright && playwright install chromium (or channel='msedge' to
use the installed Edge, which also trusts the corporate CA). Covers clicks/forms, downloads, popups, dialogs,
JavaScript, network capture (the JSON behind a page), HAR recording, and handing the login to requests."""
import re
import time
from pathlib import Path

from .robust import SystemError_


class Browser:
    """`with Browser("shots") as b: b.goto(url); b.click("text=Export")`. Failures screenshot + raise SystemError_.
    record_har='run.har' saves all traffic; block=('image','font','media') speeds up heavy pages."""

    def __init__(self, screenshots_dir="screenshots", headless=True, timeout_ms=30000, channel=None,
                 record_har=None, block=(), proxy=None, user_agent=None, viewport=None, args=()):
        self.dir = Path(screenshots_dir)
        self.headless, self.timeout, self.channel, self.n = headless, timeout_ms, channel, 0
        self.record_har, self.block, self.proxy = record_har, tuple(block), proxy
        self.user_agent, self.viewport, self.args = user_agent, viewport, list(args)
        self.dialogs, self._listeners = [], []

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        kw = {"headless": self.headless}
        if self.channel:
            kw["channel"] = self.channel   # "msedge" = installed Edge, no browser download
        if self.proxy:
            kw["proxy"] = {"server": self.proxy} if isinstance(self.proxy, str) else self.proxy
        if self.args:
            kw["args"] = self.args
        self.browser = self.pw.chromium.launch(**kw)
        self._new_context()
        return self

    def _new_context(self, storage_state=None):
        kw = {"accept_downloads": True}
        if storage_state:
            kw["storage_state"] = str(storage_state)
        if self.record_har:
            Path(self.record_har).parent.mkdir(parents=True, exist_ok=True)
            kw["record_har_path"] = str(self.record_har)
        if self.user_agent:
            kw["user_agent"] = self.user_agent
        if self.viewport:
            kw["viewport"] = {"width": self.viewport[0], "height": self.viewport[1]}
        self.ctx = self.browser.new_context(**kw)
        self.ctx.set_default_timeout(self.timeout)
        if self.block:
            types = set(self.block)
            self.ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in types
                           else route.continue_())
        self.page = self.ctx.new_page()

    def __exit__(self, et, e, tb):
        if e is not None:
            self.screenshot("exit_error")
        for c in (getattr(self, "ctx", None), getattr(self, "browser", None)):   # ctx close also writes the HAR
            try:
                c and c.close()
            except Exception:
                pass
        self.pw.stop()

    def screenshot(self, label):
        """Full-page PNG into screenshots_dir; returns path (None if it failed)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n += 1
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", label)[:60]
        p = self.dir / f"{self.n:03d}_{safe}.png"
        try:
            self.page.screenshot(path=str(p), full_page=True)
            return p
        except Exception:
            return None

    def _do(self, label, fn, retries=0):
        """Run fn; retry only reads/navigation; screenshot + SystemError_ on final failure."""
        for attempt in range(retries + 1):
            try:
                return fn()
            except Exception as e:
                if attempt == retries:
                    self.screenshot(f"fail_{label}")
                    raise SystemError_(f"{label}: {type(e).__name__}: {str(e)[:200]}", code="WEB_ACTION",
                                       url=self.page.url) from e
                time.sleep(2 * (attempt + 1))

    # ---------- navigate / wait ----------
    def goto(self, url, wait_until="domcontentloaded"):
        """Navigate (retried). wait_until: 'domcontentloaded' | 'load' | 'networkidle'."""
        return self._do("goto", lambda: self.page.goto(url, wait_until=wait_until), retries=2)

    def wait(self, sel, state="visible"):
        """Wait for an element (retried once). state: visible | attached | hidden | detached."""
        return self._do(f"wait[{sel}]", lambda: self.page.wait_for_selector(sel, state=state), retries=1)

    def wait_text(self, text, timeout_ms=None):
        """Wait until some element shows this text."""
        return self._do(f"wait_text[{text}]",
                        lambda: self.page.get_by_text(text).first.wait_for(timeout=timeout_ms or self.timeout))

    def wait_url(self, pattern, timeout_ms=None):
        """Wait until the URL matches a regex (e.g. after login redirects)."""
        rx = re.compile(pattern)
        return self._do(f"wait_url[{pattern}]", lambda: self.page.wait_for_url(rx, timeout=timeout_ms or self.timeout))

    def wait_idle(self, timeout_ms=None):
        """Wait until the network is quiet (no requests for 500 ms)."""
        return self._do("wait_idle", lambda: self.page.wait_for_load_state("networkidle",
                                                                          timeout=timeout_ms or self.timeout))

    def exists(self, sel, ms=2000):
        """True if the selector appears within ms."""
        try:
            self.page.wait_for_selector(sel, timeout=ms)
            return True
        except Exception:
            return False

    # ---------- actions ----------
    def click(self, sel):
        """Click (NOT retried: it may have submitted)."""
        return self._do(f"click[{sel}]", lambda: self.page.click(sel))

    def fill(self, sel, value):
        """Type into an input (replaces content)."""
        return self._do(f"fill[{sel}]", lambda: self.page.fill(sel, value))

    def fill_form(self, values):
        """Fill several inputs: {selector: value}."""
        for sel, value in values.items():
            self.fill(sel, str(value))

    def select(self, sel, value):
        """Choose an <option> by value or label."""
        return self._do(f"select[{sel}]", lambda: self.page.select_option(sel, value))

    def check(self, sel, on=True):
        """Tick / untick a checkbox or radio."""
        return self._do(f"check[{sel}]", lambda: self.page.set_checked(sel, on))

    def press(self, sel, key):
        """Press a key in an element, e.g. press('#search', 'Enter')."""
        return self._do(f"press[{sel}]", lambda: self.page.press(sel, key))

    def hover(self, sel):
        """Hover (opens hover menus)."""
        return self._do(f"hover[{sel}]", lambda: self.page.hover(sel))

    def login(self, user, password, user_sel, pass_sel, submit_sel, success_sel=None):
        """Fill credentials (never logged), submit, optionally wait for a post-login element."""
        self.fill(user_sel, user)
        self.fill(pass_sel, password)
        self.click(submit_sel)
        if success_sel:
            self.wait(success_sel)

    def download(self, trigger_sel, save_to):
        """Click something that starts a download; save it to save_to."""
        def _dl():
            with self.page.expect_download() as info:
                self.page.click(trigger_sel)
            Path(save_to).parent.mkdir(parents=True, exist_ok=True)
            info.value.save_as(str(save_to))
            return Path(save_to)
        return self._do(f"download[{trigger_sel}]", _dl)

    def upload(self, input_sel, file_path):
        """Set a file on an <input type=file>."""
        return self._do(f"upload[{input_sel}]", lambda: self.page.set_input_files(input_sel, str(file_path)))

    def popup(self, trigger_sel, switch=True):
        """Click something that opens a new tab/window; returns that page (and makes it current if switch)."""
        def _p():
            with self.page.expect_popup() as info:
                self.page.click(trigger_sel)
            new = info.value
            new.wait_for_load_state()
            if switch:
                self.page = new
            return new
        return self._do(f"popup[{trigger_sel}]", _p)

    def tabs(self):
        """Open pages (tabs) in this context."""
        return list(self.ctx.pages)

    def switch_tab(self, index=-1):
        """Make tab `index` current (-1 = newest)."""
        self.page = self.ctx.pages[index]
        self.page.bring_to_front()
        return self.page

    def accept_dialogs(self, accept=True, prompt_text=None):
        """Auto-answer alert/confirm/prompt boxes (default Playwright dismisses them); messages go to .dialogs."""
        def on_dialog(d):
            self.dialogs.append(d.message)
            if not accept:
                d.dismiss()
            elif prompt_text is None:
                d.accept()
            else:
                d.accept(prompt_text)
        self.page.on("dialog", on_dialog)

    # ---------- read ----------
    def text(self, sel):
        """Visible text of an element (retried once)."""
        return self._do(f"text[{sel}]", lambda: self.page.inner_text(sel), retries=1)

    def texts(self, sel):
        """Visible text of every match (list)."""
        return self._do(f"texts[{sel}]", lambda: self.page.locator(sel).all_inner_texts(), retries=1)

    def attr(self, sel, name):
        """Attribute value of the first match (e.g. href, value, data-id)."""
        return self._do(f"attr[{sel}]", lambda: self.page.get_attribute(sel, name), retries=1)

    def count(self, sel):
        """Number of elements matching sel."""
        return self.page.locator(sel).count()

    def html(self):
        """Current page HTML (after JavaScript ran)."""
        return self.page.content()

    def table(self, table_sel):
        """HTML <table> -> list of dicts keyed by header text."""
        def _t():
            hdr = self.page.eval_on_selector_all(f"{table_sel} th", "els => els.map(e => e.innerText.trim())")
            rows = self.page.eval_on_selector_all(
                f"{table_sel} tbody tr",
                "trs => trs.map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()))")
            return [dict(zip(hdr, r)) for r in rows if r]
        return self._do(f"table[{table_sel}]", _t, retries=1)

    def tables(self, match=None):
        """Every <table> on the page -> list of DataFrames (colspan/rowspan handled); match = regex filter."""
        from .htmlparse import html_tables
        return html_tables(self.html(), match=match)

    def frame(self, name_or_url):
        """An iframe (legacy portals) to call .click/.fill on."""
        f = self.page.frame(name=name_or_url) or next((f for f in self.page.frames if name_or_url in f.url), None)
        if f is None:
            raise SystemError_(f"frame {name_or_url} not found", code="WEB_FRAME")
        return f

    def pdf(self, path):
        """Save the page as PDF (headless Chromium/Edge only)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.pdf(path=str(path), format="A4", print_background=True)
        return Path(path)

    # ---------- JavaScript ----------
    def js(self, script, arg=None):
        """Run JavaScript in the page and return its (JSON-able) result: js('() => document.title'),
        js('n => window.scrollBy(0, n)', 500)."""
        return self._do("js", lambda: self.page.evaluate(script) if arg is None else self.page.evaluate(script, arg))

    def scroll_to_bottom(self, max_rounds=50, pause_ms=500):
        """Keep scrolling until the page stops growing (infinite-scroll lists); returns rounds used."""
        last = -1
        for i in range(max_rounds):
            h = self.page.evaluate("() => document.body.scrollHeight")
            if h == last:
                return i
            last = h
            self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(pause_ms)
        return max_rounds

    def local_storage(self):
        """window.localStorage as a dict (some portals keep tokens/settings there)."""
        return self.page.evaluate("() => Object.assign({}, window.localStorage)")

    # ---------- network ----------
    def capture_json(self, url_re=".*"):
        """Start collecting JSON responses whose URL matches url_re (the API behind the page). Returns a list
        that fills while you browse: [{'url', 'status', 'method', 'data'}]. Stop with stop_capture()."""
        bucket, rx = [], re.compile(url_re)

        def on_response(resp):
            if not rx.search(resp.url) or "json" not in (resp.headers.get("content-type") or ""):
                return
            try:
                data = resp.json()
            except Exception:
                return
            bucket.append({"url": resp.url, "status": resp.status, "method": resp.request.method, "data": data})
        self.page.on("response", on_response)
        self._listeners.append(("response", on_response))
        return bucket

    def log_network(self, types=("xhr", "fetch", "document")):
        """Start logging requests of these resource types: [{'method','url','status','type','content_type'}]
        -> pd.DataFrame(log) shows which API calls a page makes."""
        log, types = [], set(types)

        def on_response(resp):
            if types and resp.request.resource_type not in types:
                return
            log.append({"method": resp.request.method, "url": resp.url, "status": resp.status,
                        "type": resp.request.resource_type, "content_type": resp.headers.get("content-type", "")})
        self.page.on("response", on_response)
        self._listeners.append(("response", on_response))
        return log

    def stop_capture(self):
        """Remove capture_json / log_network listeners."""
        for event, fn in self._listeners:
            try:
                self.page.remove_listener(event, fn)
            except Exception:
                pass
        self._listeners = []

    def wait_json(self, url_re, action=None, timeout_ms=None):
        """Do action() (e.g. lambda: b.click('text=Search')) and return the JSON of the first response whose
        URL matches url_re. Not retried (the action may have submitted)."""
        rx = re.compile(url_re)

        def _w():
            with self.page.expect_response(lambda r: bool(rx.search(r.url)),
                                           timeout=timeout_ms or self.timeout) as info:
                if action:
                    action()
            r = info.value
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status} from {r.url}")
            return r.json()
        return self._do(f"wait_json[{url_re}]", _w)

    def get_json(self, url, params=None, headers=None):
        """GET an API with the browser's cookies/SSO (no new login) -> JSON. Errors classified like api_call."""
        from .api import raise_for_status
        r = self._do(f"get_json[{url}]", lambda: self.ctx.request.get(url, params=params, headers=headers),
                     retries=2)
        raise_for_status(r.status, r.url, r.text()[:300])
        return r.json()

    def set_headers(self, headers):
        """Extra HTTP headers on every request from this context."""
        self.ctx.set_extra_http_headers(headers)

    def cookies(self):
        """Cookies of this context (list of dicts)."""
        return self.ctx.cookies()

    def requests_session(self, **session_kw):
        """requests.Session carrying this browser's cookies + user agent: log in once in the browser, then call
        the site's APIs fast with api_get / paginate. session_kw go to api.http_session (verify=, proxies=)."""
        from .api import http_session
        s = http_session(**session_kw)
        for c in self.ctx.cookies():
            s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        s.headers["User-Agent"] = self.page.evaluate("() => navigator.userAgent")
        return s

    # ---------- sessions ----------
    def save_session(self, path):
        """Save cookies/SSO so the next run can skip login (treat the file as a password)."""
        self.ctx.storage_state(path=str(path))

    def load_session(self, path):
        """Start a fresh context from a saved session."""
        self.ctx.close()
        self._listeners = []
        self._new_context(storage_state=path)
