"""
title: Prefer the data behind the screen to clicking through it
order: 130
when: >-
  A robot that reads a list from a portal or an internal web app (trades, cases, statuses). Go down the ladder and
  stop at the first rung that works; screen clicks are the last resort.
tags: [api, web, json, har, html, fallback, pagination]
needs: [requests, playwright]
blocks: [RETRY_PROFILES, RobotError, SystemError_, TokenCache, api_get, api_to_df, assert_unique, attempt, bearer_session, curl_to_request, get_secret, har_entries, har_json, html_tables, http_session, paginate, safe_url, standard_columns, to_number, windows_auth_session]
decisions:
  - "The ladder: 1 the official API (documented, versioned, has an owner); 2 the JSON the portal page itself calls; 3 the HTML table; 4 screen clicks. Each rung down breaks more often when the screen changes."
  - "Find rung 2 once, at design time: record the page (Browser(record_har=...) or DevTools > Export HAR), list its JSON calls (har_entries, har_json) and rebuild one from 'Copy as cURL' (curl_to_request drops cookies, tokens and sig=). A call that cannot be replayed: keep what the page loads with b.wait_json or b.capture_json."
  - "Sessions: rung 1 the robot's own OAuth client (bearer_session + TokenCache); rung 2 the browser's login (b.requests_session) or Windows SSO. Never a token copied from DevTools: it expires, and it is a person's identity."
  - "Pages of 100 until an empty page. Complete only when the rows equal the total the source reports and every id is unique; otherwise SystemError_ INCOMPLETE (a record added while paging shifts the pages)."
  - "Retries per layer: http_session retries each page 3 times (0.5 s backoff, honours Retry-After), so a blip on page 7 does not restart the pull; attempt() tries the API route twice, 2 s apart (RETRY_PROFILES['read'] with tries=2), then the HTML table once."
  - "401 / 403 is RobotError AUTH: attempt stops at once, no fallback. The HTML route uses the same session; fix the login, never retry it."
  - "Both routes return the same columns and types (standard_columns, to_number, ids as text) and the name of the route used."
governance:
  - "Rungs 2 and 3 use an interface its owner did not promise: record the endpoint in the design document, ask the owner for an official API, and re-test after every portal release."
  - "Read-only: this block only GETs. Anything that changes data goes through the submit-once block, never a replayed POST from DevTools."
  - "HAR files and cURL text can hold cookies and customer data: sanitise (har_sanitize; Browser does it), keep them out of the code repository, delete them after the design."
  - "Logs carry the route and row counts, never records; errors carry the URL without its query string (safe_url)."
  - "A fallback run is visible: the route used goes in the log and on the report, so a silent switch to the screen is seen."
"""
import importlib.util
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from toolbank import (RETRY_PROFILES, RobotError, SystemError_, TokenCache, api_get, api_to_df, assert_unique, attempt,
                      bearer_session, curl_to_request, get_secret, har_entries, har_json, html_tables, http_session,
                      paginate, safe_url, standard_columns, to_number, windows_auth_session)

PAGE = 100                                                # records per page asked; a server may send fewer
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


def tidy(df, route, total, url):
    """Same columns and types whichever route read the data; complete and unique. Returns (route, df)."""
    df = df if len(df.columns) else pd.DataFrame(columns=list(COLUMNS))   # a day with no records
    df = standard_columns(df, COLUMNS, strict=True, keep_others=False)[list(COLUMNS)]
    df = df.assign(trade_id=df["trade_id"].astype("string"), amount=to_number(df["amount"]))
    if total is None or len(df) != int(total):
        raise SystemError_(f"{route}: {len(df)} rows, source says {total}", code="INCOMPLETE", url=safe_url(url))
    assert_unique(df, "trade_id")
    return route, df


def api_route(session, url):
    """Rungs 1-2: every record, page by page, checked against the total the API reports."""
    total = (api_get(session, url, params={"page": 1, "pageSize": 1}) or {}).get("total")
    records = paginate(session, url, style="page", items_key="items", size_param="pageSize", size=PAGE)
    return tidy(api_to_df(records), "api", total, url)


def html_route(session, url, count_re=r"of (\d+) trades"):
    """Rung 3: the table on the portal's own page (b.tables() in a browser), checked against its 'of N' count."""
    html = api_get(session, url, expect_json=False).text
    shown = re.search(count_re, html)
    return tidy(html_tables(html, match="Trade ID")[0], "html", shown and shown.group(1), url)


def read_trades(session, api_url, page_url, *, log=print, sleep=time.sleep, clock=time.monotonic):
    """The API first, the HTML table once as the fallback (read-only, so retrying is safe). Returns (route, df)."""
    return attempt(lambda: api_route(session, api_url), fallbacks=[lambda: html_route(session, page_url)],
                   name="read trades", log=log, sleep=sleep, clock=clock, **dict(RETRY_PROFILES["read"], tries=2))


TRADES = [{"id": f"T{i}", "amount": 1000.5 * i, "ccy": "SGD"} for i in range(1, 6)]     # the demo portal's data
PORTAL_PAGE = ("<p>1-5 of 5 trades</p><table><tr><th>Trade ID</th><th>Amount</th><th>Currency</th></tr>"
               + "".join(f"<tr><td>{t['id']}</td><td>{t['amount']:,.2f}</td><td>{t['ccy']}</td></tr>" for t in TRADES)
               + "</table>")


def demo(tmp):
    """Design time: a HAR shows the JSON call, 'Copy as cURL' loses its secrets. Run time on a 127.0.0.1 portal:
    the API gives all 5 trades over 3 pages; with the API at 500 the HTML table gives the same frame; 401 stops."""
    call = lambda path, mime, text: {"request": {"method": "GET", "url": f"https://portal.bank.example{path}"},
                                     "response": {"status": 200, "content": {"mimeType": mime, "text": text}}}
    entries = [call("/api/trades?page=1", "application/json", '{"total": 5}'), call("/trades", "text/html", "")]
    (tmp / "portal.har").write_text(json.dumps({"log": {"entries": entries}}))
    calls, bodies = json_calls(tmp / "portal.har")
    assert len(calls) == 1 and bodies[0]["data"] == {"total": 5}
    req = curl_to_request("curl 'https://portal.bank.example/api/trades?page=1&sig=abc' -H 'Cookie: s=1'")
    assert req["params"] == {"page": "1"} and sorted(req["dropped"]) == ["Cookie", "sig"]
    if importlib.util.find_spec("requests") is None:
        return                                            # the run-time half needs requests (pip install requests)

    state = {"api": 200}

    class Portal(BaseHTTPRequestHandler):
        log_message = lambda self, *args: None            # quiet

        def do_GET(self):
            u = urlsplit(self.path)
            q, api = {k: int(v[0]) for k, v in parse_qs(u.query).items()}, u.path == "/api/trades"
            n = min(q.get("pageSize", 2), 2)              # this server sends at most 2 per page
            body = (json.dumps({"items": TRADES[(q["page"] - 1) * n:q["page"] * n], "total": len(TRADES)}) if api
                    else PORTAL_PAGE).encode()
            self.send_response(state["api"] if api else 200)
            self.send_header("Content-Type", "application/json" if api else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, slept, log = f"http://127.0.0.1:{srv.server_port}", [], []
    s = http_session(retries=0)                           # demo only: no transport retries, no proxy
    s.trust_env = False
    go = lambda: read_trades(s, f"{base}/api/trades", f"{base}/trades", log=log.append, sleep=slept.append)
    try:
        route, api = go()
        assert route == "api" and api["trade_id"].tolist() == [t["id"] for t in TRADES] and not log
        state["api"] = 500
        route, html = go()
        pd.testing.assert_frame_equal(html, api)
        assert route == "html" and slept == [2.0, 4.0] and log[-1] == "read trades: ok on fallback 1 try 1"
        state["api"], before = 401, len(log)
        try:
            go()
        except RobotError as e:
            assert e.code == "AUTH" and len(log) == before and slept == [2.0, 4.0]    # no retry, no fallback
        else:
            raise AssertionError("expected AUTH")
    finally:
        srv.shutdown()
        srv.server_close()
