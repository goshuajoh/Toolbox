import json

import pandas as pd
import pytest

from toolbank import *
from tests._server import serve

requests = pytest.importorskip("requests")


# ---------- htmlparse ----------

SPAN_TABLE = """
<table id="x"><tr><th>Desk</th><th colspan="2">Q1</th></tr>
<tr><td rowspan="2">FX</td><td>1</td><td>2</td></tr>
<tr><td>3</td><td>4</td></tr>
<tr><td>Rates<br>EM</td><td>5</td><td>6
</table>
<table><thead><tr><th colspan=2>Trade</th></tr><tr><th>Id</th><th>Ccy</th></tr></thead>
<tbody><tr><td>T1</td><td>USD</td></tr></tbody></table>
"""


def test_html_tables_spans_and_multirow_header():
    t1, t2 = html_tables(SPAN_TABLE)
    assert list(t1.columns) == ["Desk", "Q1", "Q1_2"]
    assert t1.values.tolist() == [["FX", "1", "2"], ["FX", "3", "4"], ["Rates\nEM", "5", "6"]]
    assert list(t2.columns) == ["Trade | Id", "Trade | Ccy"] and t2.iloc[0].tolist() == ["T1", "USD"]
    assert len(html_tables(SPAN_TABLE, match="Trade")) == 1
    assert html_tables(SPAN_TABLE, header=False)[0].shape == (4, 3)


def test_html_text_links_forms():
    page = """<html><head><style>.a{}</style><script>var x=1;</script></head><body>
    <h1>Title&nbsp;here</h1><p>Line <b>one</b></p><a href="/a?x=1">A link</a> <a href="#top">skip</a>
    <form action="/post" method="post"><input type="hidden" name="csrf" value="tok"><input name="user">
    <input type="checkbox" name="all" checked><input type="checkbox" name="no">
    <select name="ccy"><option value="USD">USD</option><option value="SGD" selected>SGD</option></select>
    <textarea name="note">hi</textarea><input type="submit" name="go" value="Go"></form></body></html>"""
    assert html_text(page) == "Title here\nLine one\nA link skip\nUSD\nSGD\nhi"
    assert html_text("<table><tr><td>FX</td><td>1</td></tr><tr><td>EM</td><td>2</td></tr></table>") == "FX 1\nEM 2"
    links = html_links(page, base_url="https://x.test/p/")
    assert links["url"].tolist() == ["https://x.test/a?x=1"]
    form = html_forms(page, base_url="https://x.test/p/")[0]
    assert form["action"] == "https://x.test/post" and form["method"] == "POST"
    assert form["fields"] == {"csrf": "tok", "user": "", "all": "on", "no": None, "ccy": "SGD", "note": "hi"}
    assert strip_tags(pd.Series(["<b>x</b> y", None])).tolist()[0] == "x y"


def test_html_json_embedded():
    page = ('<script id="__NEXT_DATA__" type="application/json">{"props": {"rows": [1, 2]}}</script>'
            '<script>var cfg = {"a": "x}y", "b": [1, {"c": 2}]}; window.__STATE__ = [{"id": 7}]; let js = {a: 1};'
            '</script><script type="application/ld+json">{"@type": "Org"}</script><script></script>')
    got = {x["source"]: x["data"] for x in html_json(page)}
    assert got == {"__NEXT_DATA__": {"props": {"rows": [1, 2]}}, "cfg": {"a": "x}y", "b": [1, {"c": 2}]},
                   "__STATE__": [{"id": 7}], "application/ld+json": {"@type": "Org"}}


def test_html_select():
    pytest.importorskip("bs4")
    assert html_select('<div class="a">1</div><div class="a" data-id="7">2</div>', "div.a") == ["1", "2"]
    assert html_select('<div class="a" data-id="7">2</div>', "div.a", attr="data-id") == ["7"]


# ---------- api ----------

def _pages(req):
    page = int(req.query.get("page", 1))
    return 200, {}, {"items": [{"id": page * 10 + i} for i in range(2)] if page <= 2 else []}


def test_api_calls_pagination_and_errors():
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        return (503, {}, b"busy") if calls["n"] == 1 else (200, {}, {"ok": True})

    routes = {
        ("GET", "/odata"): lambda r: (200, {}, {"value": [1, 2], "@odata.nextLink": "/odata2?skip=2"}),
        ("GET", "/odata2"): lambda r: (200, {}, {"value": [3]}),
        ("GET", "/pages"): _pages,
        ("GET", "/offset"): lambda r: (200, {}, {"data": list(range(int(r.query["offset"]),
                                                                      min(int(r.query["offset"]) + 2, 5)))}),
        ("GET", "/cursor"): lambda r: (200, {}, {"results": ["a"] if "cursor" not in r.query else ["b"],
                                                  "next_cursor": None if "cursor" in r.query else "c1"}),
        ("GET", "/linkh"): lambda r: (200, {"Link": '</linkh2>; rel="next"'}, [1]),
        ("GET", "/linkh2"): lambda r: (200, {}, [2]),
        ("GET", "/401"): lambda r: (401, {}, b"no"),
        ("GET", "/404"): lambda r: (404, {}, b"missing"),
        ("GET", "/flaky"): flaky,
        ("GET", "/text"): lambda r: (200, {}, "<html>not json</html>"),
        ("POST", "/echo"): lambda r: (200, {}, json.loads(r.body)),
    }
    with serve(routes) as (base, log):
        s = http_session(retries=2, backoff=0)
        assert list(paginate(s, base + "/odata")) == [1, 2, 3]
        assert [x["id"] for x in paginate(s, base + "/pages", style="page")] == [10, 11, 20, 21]
        assert list(paginate(s, base + "/offset", style="offset", size=2)) == [0, 1, 2, 3, 4]
        assert list(paginate(s, base + "/cursor", style="cursor")) == ["a", "b"]
        assert list(paginate(s, base + "/linkh", style="link_header")) == [1, 2]
        assert api_get(s, base + "/flaky") == {"ok": True}           # 503 retried by the session
        assert api_post(s, base + "/echo", {"a": 1}) == {"a": 1}
        with pytest.raises(RobotError) as e:
            api_get(s, base + "/401")
        assert e.value.code == "AUTH" and not isinstance(e.value, (BusinessError, SystemError_))
        with pytest.raises(BusinessError) as e:
            api_get(s, base + "/404")
        assert e.value.code == "HTTP_404"
        with pytest.raises(BusinessError):
            api_get(s, base + "/text")
        with pytest.raises(SystemError_):
            api_get(http_session(retries=0), "http://127.0.0.1:9/nothing")
        assert api_to_df([{"a": {"b": 1}}]).columns.tolist() == ["a.b"]


def test_token_cache_bearer_download_upload_graph(tmp_path):
    hits = {"token": 0}

    def token(req):
        hits["token"] += 1
        assert b"grant_type=client_credentials" in req.body
        return 200, {}, {"access_token": f"t{hits['token']}", "expires_in": 3600}

    def whoami(req):
        return 200, {}, {"auth": req.headers.get("Authorization")}

    routes = {("POST", "/token"): token, ("GET", "/me"): whoami,
              ("GET", "/file.bin"): lambda r: (200, {"Content-Type": "application/octet-stream"}, b"abc" * 1000),
              ("POST", "/upload"): lambda r: (200, {}, {"size": len(r.body)}),
              ("GET", "/users/u1/messages"): lambda r: (200, {}, {"value": [{"id": 1}],
                                                                  "@odata.nextLink": "/users/u1/messages2"}),
              ("GET", "/users/u1/messages2"): lambda r: (200, {}, {"value": [{"id": 2}]}),
              ("POST", "/users/u1/sendMail"): lambda r: (202, {}, b""),
              ("PUT", "/drives/d1/root:/f/a.txt:/content"): lambda r: (201, {}, {"name": "a.txt"})}
    with serve(routes) as (base, log):
        tc = TokenCache(base + "/token", "id", "secret", scope="api://x/.default")
        s = bearer_session(tc)
        assert api_get(s, base + "/me") == {"auth": "Bearer t1"}
        assert api_get(s, base + "/me") == {"auth": "Bearer t1"} and hits["token"] == 1   # cached
        assert api_get(api_key_session("k1", header="X-Key"), base + "/me")["auth"] is None
        content = b"abc" * 1000
        import hashlib
        p = download_file(s, base + "/file.bin", tmp_path / "d" / "f.bin", sha256=hashlib.sha256(content).hexdigest())
        assert p.read_bytes() == content
        with pytest.raises(BusinessError):
            download_file(s, base + "/file.bin", tmp_path / "bad.bin", sha256="0" * 64)
        assert not (tmp_path / "bad.bin").exists() and not list(tmp_path.glob(".dl-*"))
        (tmp_path / "up.txt").write_text("hello")
        assert upload_file(s, base + "/upload", tmp_path / "up.txt")["size"] > 5
        assert [m["id"] for m in graph_get_all(s, "/users/u1/messages", base=base)] == [1, 2]
        assert graph_send_mail(s, "u1", "a@x.test", "Hi", "<b>x</b>", base=base) is None
        (tmp_path / "a.txt").write_text("x")
        assert graph_upload(s, "d1", "f/a.txt", tmp_path / "a.txt", base=base)["name"] == "a.txt"


def test_rate_limiter_and_signatures():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s

    rl = RateLimiter(2, per=1.0, clock=lambda: t["now"], sleep=sleep)
    for _ in range(5):
        rl.wait()
    assert t["now"] == pytest.approx(1.5)
    sig = sign_body(b'{"a":1}', "s3cret")
    assert verify_signature(b'{"a":1}', "s3cret", sig) and not verify_signature(b'{"a":2}', "s3cret", sig)
    assert not verify_signature(b"x", "s3cret", None)


def test_post_webhook_signed():
    got = {}

    def hook(req):
        got["ok"] = verify_signature(req.body, "k", req.headers.get("X-Signature"))
        return 200, {}, {"received": True}

    with serve({("POST", "/hook"): hook}) as (base, log):
        assert post_webhook(base + "/hook", {"run": "r1"}, secret="k") == {"received": True}
        assert got["ok"]
        assert post_webhook(base + "/hook", {"x": 1}, dry_run=True) is None


def test_curl_to_request_bash_and_cmd():
    bash = """curl 'https://api.x.test/v1/trades?desk=FX&page=2' \\
  -H 'accept: application/json' \\
  -H 'authorization: Bearer SECRET' \\
  -H 'content-type: application/json' \\
  -b 'sid=SECRET' \\
  --data-raw '{"from":"2026-01-01"}' \\
  --compressed"""
    r = curl_to_request(bash)
    assert r["method"] == "POST" and r["url"] == "https://api.x.test/v1/trades"
    assert r["params"] == {"desk": "FX", "page": "2"} and r["json"] == {"from": "2026-01-01"}
    assert {k.lower() for k in r["headers"]} == {"accept", "content-type"}
    cmd = ('curl ^"https://x.test/api?a=1^" ^\n  -H ^"accept: */*^" ^\n'
           '  --data-raw ^"^{^\\^"k^\\^":1^}^" ^\n  --compressed')
    r = curl_to_request(cmd)
    assert r["url"] == "https://x.test/api" and r["params"] == {"a": "1"} and r["data"] == '{"k":1}'
    assert curl_to_request("curl https://x.test/a")["method"] == "GET"


def test_har_helpers(tmp_path):
    har = {"log": {"entries": [
        {"startedDateTime": "2026-01-01T00:00:00Z", "time": 12.5, "_resourceType": "fetch",
         "request": {"method": "GET", "url": "https://x.test/api/items"},
         "response": {"status": 200, "content": {"mimeType": "application/json", "size": 9, "text": '{"a": 1}'}}},
        {"startedDateTime": "2026-01-01T00:00:01Z", "time": 3, "request": {"method": "GET", "url": "https://x.test/logo.png"},
         "response": {"status": 200, "content": {"mimeType": "image/png", "size": 100}}},
        {"startedDateTime": "2026-01-01T00:00:02Z", "time": 3, "request": {"method": "POST", "url": "https://x.test/api/b"},
         "response": {"status": 200, "content": {"mimeType": "application/json", "encoding": "base64",
                                                  "text": "eyJiIjogMn0="}}}]}}
    p = tmp_path / "x.har"
    p.write_text(json.dumps(har))
    df = har_entries(p)
    assert len(df) == 3 and df.loc[0, "type"] == "fetch"
    assert [x["data"] for x in har_json(p, r"/api/")] == [{"a": 1}, {"b": 2}]


# ---------- web (Playwright) ----------

INDEX = """<html><head><title>Portal</title></head><body>
<button id="go" onclick="load()">Load</button>
<table id="t"><thead><tr><th>Id</th><th>Amount</th></tr></thead><tbody></tbody></table>
<a id="pop" href="/popup" target="_blank">Open</a> <a id="dl" href="/file.csv">Download</a>
<button id="ask" onclick="document.getElementById('ans').innerText = confirm('Sure?') ? 'yes' : 'no'">Ask</button>
<div id="ans"></div><input id="q"><input type="checkbox" id="cb"><div id="list"></div>
<script>
function load(){ fetch('/api/items').then(r => r.json()).then(j => { const tb = document.querySelector('#t tbody');
  j.items.forEach(it => { const tr = document.createElement('tr'); tr.innerHTML = '<td>' + it.id + '</td><td>' + it.amount + '</td>'; tb.appendChild(tr); }); }); }
let n = 0; function more(){ for (let i = 0; i < 20 && n < 100; i++, n++) { const d = document.createElement('div');
  d.style.height = '60px'; d.className = 'row'; d.innerText = 'row ' + n; document.getElementById('list').appendChild(d); } }
more(); window.addEventListener('scroll', () => { if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 10) more(); });
</script></body></html>"""


def _web_routes():
    return {("GET", "/"): lambda r: (200, {}, INDEX),
            ("GET", "/api/items"): lambda r: (200, {}, {"items": [{"id": 1, "amount": 10.5}, {"id": 2, "amount": 20}]}),
            ("GET", "/popup"): lambda r: (200, {}, "<html><body><h1>Popup page</h1></body></html>"),
            ("GET", "/file.csv"): lambda r: (200, {"Content-Type": "text/csv",
                                                   "Content-Disposition": 'attachment; filename="x.csv"'}, b"a,b\n1,2\n"),
            ("GET", "/login"): lambda r: (200, {"Set-Cookie": "sid=abc; Path=/"}, "<html><body>ok</body></html>"),
            ("GET", "/api/secure"): lambda r: ((200, {}, {"ok": True}) if "sid=abc" in (r.headers.get("Cookie") or "")
                                               else (401, {}, b"login first"))}


@pytest.fixture(scope="module")
def browser_ok():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")


def test_browser_network_js_dialogs_popups(tmp_path, browser_ok):
    har = tmp_path / "run.har"
    with serve(_web_routes()) as (base, log):
        with Browser(tmp_path / "shots", record_har=har) as b:
            b.goto(base + "/")
            assert b.js("() => document.title") == "Portal"
            assert b.count(".row") == 20
            assert b.scroll_to_bottom(pause_ms=150) <= 50 and b.count(".row") == 100
            cap = b.capture_json(r"/api/")
            net = b.log_network()
            data = b.wait_json(r"/api/items", lambda: b.click("#go"))
            assert data["items"][0]["id"] == 1
            b.wait("#t tbody tr")
            assert cap and cap[0]["data"]["items"][1]["amount"] == 20 and cap[0]["method"] == "GET"
            assert any(x["url"].endswith("/api/items") and x["type"] == "fetch" for x in net)
            b.stop_capture()
            t = b.tables(match="Amount")[0]
            assert t.columns.tolist() == ["Id", "Amount"] and t["Amount"].tolist() == ["10.5", "20"]
            assert b.table("#t") == [{"Id": "1", "Amount": "10.5"}, {"Id": "2", "Amount": "20"}]
            b.accept_dialogs()
            b.click("#ask")
            assert b.text("#ans") == "yes" and b.dialogs == ["Sure?"]
            b.fill_form({"#q": "abc"})
            b.press("#q", "End")
            b.check("#cb")
            assert b.js("() => document.querySelector('#cb').checked") is True
            assert b.attr("#pop", "href") == "/popup" and b.texts("th") == ["Id", "Amount"]
            p = b.download("#dl", tmp_path / "out" / "x.csv")
            assert p.read_text() == "a,b\n1,2\n"
            assert "Download" in b.html()
            b.js("() => localStorage.setItem('k', 'v')")
            assert b.local_storage() == {"k": "v"}
            pdf = b.pdf(tmp_path / "page.pdf")
            assert pdf.read_bytes()[:4] == b"%PDF"
            pop = b.popup("#pop")
            assert pop.url.endswith("/popup") and b.text("h1") == "Popup page" and len(b.tabs()) == 2
            b.switch_tab(0)
            assert b.page.url == base + "/"
            b.page.set_default_timeout(500)
            with pytest.raises(SystemError_) as e:
                b.click("#does-not-exist")
            assert e.value.code == "WEB_ACTION" and list((tmp_path / "shots").glob("*fail_click*.png"))
        assert har.exists()
        df = har_entries(har)
        assert df["url"].str.endswith("/api/items").any()
        assert har_json(har, r"/api/items")[0]["data"]["items"][0]["id"] == 1


def test_browser_session_handoff_to_requests(tmp_path, browser_ok):
    with serve(_web_routes()) as (base, log):
        with Browser(tmp_path / "shots") as b:
            b.goto(base + "/")
            with pytest.raises(RobotError) as e:
                b.get_json(base + "/api/secure")
            assert e.value.code == "AUTH"
            b.goto(base + "/login")
            assert b.get_json(base + "/api/secure") == {"ok": True}
            s = b.requests_session()
            assert api_get(s, base + "/api/secure") == {"ok": True}
            assert any(c["name"] == "sid" for c in b.cookies())
            b.save_session(tmp_path / "state.json")
        with Browser(tmp_path / "shots") as b2:
            b2.load_session(tmp_path / "state.json")
            assert b2.get_json(base + "/api/secure") == {"ok": True}


def test_browser_blocks_resource_types(tmp_path, browser_ok):
    routes = _web_routes()
    routes[("GET", "/img")] = lambda r: (200, {}, '<html><body><img src="/pic.png"></body></html>')
    routes[("GET", "/pic.png")] = lambda r: (200, {"Content-Type": "image/png"}, b"\x89PNG")
    with serve(routes) as (base, log):
        with Browser(tmp_path / "s", block=("image",)) as b:
            b.goto(base + "/img")
            b.wait_idle()
        assert not any(path == "/pic.png" for _, path, _, _ in log)
