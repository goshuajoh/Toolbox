"""
title: Prefer the data behind the screen to clicking through it
order: 130
when: >-
  A robot that reads a list from a portal or an internal web app (trades, cases, statuses). Go down the ladder and
  stop at the first rung that works; screen clicks are the last resort.
tags: [api, screens, retry]
needs: [requests, playwright]
blocks: [RETRY_PROFILES, BusinessError, RobotError, SystemError_, TokenCache, api_get, api_to_df, attempt, bearer_session, curl_to_request, get_secret, har_entries, har_json, html_tables, http_session, paginate, safe_url, standard_columns, to_number, windows_auth_session]
decisions:
  - "The ladder: 1 the official API (documented, versioned, has an owner); 2 the JSON the portal page itself calls; 3 the HTML table; 4 screen clicks. Each rung down breaks more often when the screen changes."
  - "Find rung 2 once, at design time: record the page (Browser(record_har=...) or DevTools > Export HAR), list its JSON calls (har_entries, har_json) and rebuild one from 'Copy as cURL' (curl_to_request drops cookies, tokens and sig=). A call that cannot be replayed: keep what the page loads with b.wait_json or b.capture_json."
  - "Sessions: rung 1 the robot's own OAuth client (bearer_session + TokenCache); rung 2 the browser's login (b.requests_session) or Windows SSO. Never a token copied from DevTools: it expires, and it is a person's identity."
  - "Pages of 100 until an empty page. Complete only when the rows equal the total the source reports and every id is unique; otherwise SystemError_ INCOMPLETE (a record added or removed while paging shifts the pages). Both routes return the same columns and types (standard_columns, to_number, ids as trimmed text) and the name of the route used."
  - "Retries per layer: http_session retries each page 3 times (0.5 s backoff, honours Retry-After), so a blip on page 7 does not restart the pull; attempt() tries the API route twice, 2 s apart (RETRY_PROFILES['read'] with tries=2), then, 4 s later, the HTML table once."
  - "A 5xx, a network error, a moved endpoint (404, 410), a reply that is not JSON (a login page), a renamed field or records that are not objects (SHAPE) is SystemError_: retried, then the next rung, so a portal release that moves or reshapes the JSON call falls back to the table. 401 / 403 (RobotError AUTH) and any other 4xx (BusinessError) stop at once, no fallback: the HTML route uses the same session, and a 400 can be the token request refusing the robot's secret. Fix the login or the request, never retry it."
  - "A read changes nothing, so it is not irreversible: the retries and the fallback need no already_done. attempt raises a bug (TypeError, AttributeError) at once (exit 5) and reports a KeyError or ValueError as bad data (BAD_DATA, exit 2), so api_route and tidy turn a changed reply into SHAPE themselves."
governance:
  - "Rungs 2 and 3 use an interface its owner did not promise: record the endpoint in the design document, ask the owner for an official API, and re-test after every portal release."
  - "Read-only: this block only GETs. Anything that changes data goes through the submit-once block, never a replayed POST from DevTools."
  - "HAR files and cURL text can hold cookies and customer data: sanitise (har_sanitize; Browser does it), keep them out of the code repository, delete them after the design."
  - "Logs carry the route and row counts, never records; a switch to the fallback is logged and the route goes on the report. A failed call keeps its code, HTTP status and the URL without its query string (safe_url), never the reply body: it can hold records."
"""
import re
from contextlib import contextmanager

import pandas as pd

from toolbank import (RETRY_PROFILES, BusinessError, RobotError, SystemError_, TokenCache, api_get, api_to_df, attempt,
                      bearer_session, get_secret, har_entries, har_json, html_tables, paginate, safe_url,
                      standard_columns, to_number, windows_auth_session)

COLUMNS = {"trade_id": ["Trade ID", "id"], "amount": ["Amount"], "ccy": ["Currency"]}


def official_session(token_url, client_id, scope):
    """Rung 1: the robot's own OAuth client; the secret comes from the vault, the token renews itself."""
    return bearer_session(TokenCache(token_url, client_id, get_secret("PORTAL_CLIENT_SECRET"), scope=scope))


def portal_session(browser=None):
    """Rung 2: the browser's login (after the login block) or, for an intranet site, Windows SSO."""
    return browser.requests_session() if browser is not None else windows_auth_session()


def json_calls(har_path, url_re="/api/"):
    """Design time: the JSON calls a recorded page made, and their bodies (fields, paging, totals)."""
    calls = har_entries(har_path)
    return calls[calls["mime"].str.contains("json") & calls["url"].str.contains(url_re)], har_json(har_path, url_re)


@contextmanager
def rung(route, url):
    """Errors of one rung, without the reply body: 5xx, network, moved, not JSON -> SystemError_; others stop."""
    try:
        yield
    except RobotError as e:                               # 404 / 410: the call moved; NOT_JSON: a login page came back
        moved = e.retryable or e.code in ("HTTP_404", "HTTP_410", "NOT_JSON")
        kind = SystemError_ if moved else BusinessError if isinstance(e, BusinessError) else RobotError
        raise kind(f"{route} call failed", code=e.code, **{"url": safe_url(url), **e.context}) from None


def tidy(df, route, total, url):
    """Same columns and types whichever route read the data; complete and unique. Returns (route, df)."""
    df = df if len(df.columns) else pd.DataFrame(columns=list(COLUMNS))   # a day with no records
    try:
        df = standard_columns(df, COLUMNS, strict=True, keep_others=False)[list(COLUMNS)]
    except KeyError as e:                                 # MissingColumns: a renamed field, not bad data
        raise SystemError_(f"{route}: fields changed ({type(e).__name__})", code="SHAPE", url=safe_url(url)) from None
    df = df.assign(trade_id=df["trade_id"].astype("string").str.strip(), amount=to_number(df["amount"]))
    n = int(total) if str(total).isdecimal() else None    # the count the source reports
    if n != len(df) or df["trade_id"].duplicated().any():
        raise SystemError_(f"{route}: {len(df)} rows, {df['trade_id'].nunique()} ids, source says {total}",
                           code="INCOMPLETE", url=safe_url(url))
    return route, df


def api_route(session, url):
    """Rungs 1-2: every record, page by page, checked against the total the API reports."""
    with rung("api", url):
        first = api_get(session, url, params={"page": 1, "pageSize": 1})
        rows = list(paginate(session, url, style="page", items_key="items", size_param="pageSize", size=100))
    if not all(isinstance(r, dict) for r in rows):        # e.g. a list of ids: the reply changed shape, not a bug
        raise SystemError_("api: records are not objects", code="SHAPE", url=safe_url(url))
    return tidy(api_to_df(rows), "api", first.get("total") if isinstance(first, dict) else None, url)


def html_route(session, url, count_re=r"of (\d+) trades"):
    """Rung 3: the table on the portal's own page (b.tables() in a browser), checked against its 'of N' count."""
    with rung("html", url):
        html = api_get(session, url, expect_json=False).text
    shown = re.search(count_re, html)
    return tidy(html_tables(html, match="Trade ID")[0], "html", shown and shown.group(1), url)


def read_trades(session, api_url, page_url, *, log=print):
    """The API first, the HTML table once as the fallback (read-only, so retrying is safe). Returns (route, df)."""
    return attempt(lambda: api_route(session, api_url), fallbacks=[lambda: html_route(session, page_url)],
                   name="read trades", log=log, **dict(RETRY_PROFILES["read"], tries=2))


# --- demo ---


def demo(tmp):
    """Design time: a HAR shows the JSON call, 'Copy as cURL' loses its secrets. A 127.0.0.1 portal: the API gives 5
    trades over 3 pages; at 500, 404, a renamed field or a list of ids the HTML table gives the same frame, no record
    logged; 401 stops at once."""
    import importlib.util
    import json
    import threading
    from functools import partial
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest import mock
    from urllib.parse import parse_qs, urlsplit

    from toolbank import curl_to_request, http_session

    har = [{"request": {"method": "GET", "url": f"https://portal.bank.example{path}"},
            "response": {"status": 200, "content": {"mimeType": mime, "text": text}}} for path, mime, text in
           [("/api/trades?page=1", "application/json", '{"total": 5}'), ("/trades", "text/html", "")]]
    (tmp / "portal.har").write_text(json.dumps({"log": {"entries": har}}))
    calls, bodies = json_calls(tmp / "portal.har")
    assert len(calls) == 1 and bodies[0]["data"] == {"total": 5}
    req = curl_to_request("curl 'https://portal.bank.example/api/trades?page=1&sig=abc' -H 'Cookie: s=1'")
    assert req["params"] == {"page": "1"} and sorted(req["dropped"]) == ["Cookie", "sig"]
    if importlib.util.find_spec("requests") is None:
        return                                            # the run-time half needs requests (pip install requests)
    trades = [{"id": f"T{i}", "amount": 1000.5 * i, "ccy": "SGD"} for i in range(1, 6)]
    state = {"status": 200, "id": "id", "ids_only": False}   # what the fake portal's API does
    page = "<p>1-5 of 5 trades</p><table><tr><th>Trade ID</th><th>Amount</th><th>Currency</th></tr>" + "".join(
        f"<tr><td>{t['id']}</td><td>{t['amount']:,.2f}</td><td>{t['ccy']}</td></tr>" for t in trades) + "</table>"

    class Portal(BaseHTTPRequestHandler):                 # HTTP/1.0: the body ends when the connection closes
        log_message = lambda self, *args: None            # quiet
        def do_GET(self):
            u = urlsplit(self.path)
            q, api = {k: int(v[0]) for k, v in parse_qs(u.query).items()}, u.path == "/api/trades"
            n = min(q.get("pageSize", 2), 2)              # this server sends at most 2 per page
            items = [{state["id"] if k == "id" else k: v for k, v in t.items()} for t in trades]
            items = [t["id"] for t in trades] if state["ids_only"] else items
            body = json.dumps({"items": items[(q["page"] - 1) * n:q["page"] * n], "total": 5}) if api else page
            self.send_response(state["status"] if api else 200)
            self.send_header("Content-Type", "application/json" if api else "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    slept, log = [], []
    with ThreadingHTTPServer(("127.0.0.1", 0), Portal) as srv, \
            mock.patch.dict(globals(), attempt=partial(attempt, sleep=slept.append)):   # no real waits; restored
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base, s = f"http://127.0.0.1:{srv.server_port}", http_session(retries=0)
        s.trust_env = False                               # demo only: no transport retries, no proxy
        go = lambda: read_trades(s, f"{base}/api/trades", f"{base}/trades", log=log.append)
        try:
            route, api = go()
            assert route == "api" and api["trade_id"].tolist() == [t["id"] for t in trades] and not log
            for change in ({"status": 500}, {"status": 404}, {"id": "ref"}, {"ids_only": True}):
                state.update({"status": 200, "id": "id", "ids_only": False, **change})   # down; moved; renamed; ids
                slept[:] = []
                route, html = go()
                pd.testing.assert_frame_equal(html, api)
                assert route == "html" and slept == [2.0, 4.0] and log[-1] == "read trades: ok on fallback 1 try 1"
            assert "SGD" not in " ".join(log)             # the 500 and 404 replies held records; the log does not
            state.update(status=401, id="id", ids_only=False)
            before = len(log)
            try:
                go()
                raise AssertionError("expected AUTH")
            except RobotError as e:
                assert e.code == "AUTH" and len(log) == before and slept == [2.0, 4.0]    # no retry, no fallback
        finally:
            srv.shutdown()
