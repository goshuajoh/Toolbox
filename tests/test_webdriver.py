import os

import pytest

from toolbank import *
from tests._server import serve
from tests.test_api_html_web import _web_routes

CHROMIUM = os.environ.get("TOOLBANK_CHROME", "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")


@pytest.fixture(scope="module")
def chrome_kw(tmp_path_factory):
    pytest.importorskip("selenium")
    cdp = pytest.importorskip("chromedriver_py")     # pip install chromedriver-py==<your Chrome major version>
    if not os.path.exists(CHROMIUM):
        pytest.skip("no Chromium build for Selenium here")
    return {"browser": "chrome", "binary": CHROMIUM, "driver_path": cdp.binary_path, "args": ("--no-sandbox",),
            "download_dir": tmp_path_factory.mktemp("dl")}


def test_selenium_helpers(chrome_kw, tmp_path):
    routes = _web_routes()
    routes[("GET", "/form")] = lambda r: (200, {}, """<html><body><iframe id="f" src="/inner"></iframe>
        <select id="ccy"><option value="USD">US Dollar</option><option value="SGD">Singapore Dollar</option></select>
        <input id="name"></body></html>""")
    routes[("GET", "/inner")] = lambda r: (200, {}, "<html><body><p id='in'>inside frame</p></body></html>")
    with serve(routes) as (base, log):
        with webdriver_session(screenshots_dir=tmp_path / "shots", **chrome_kw) as d:
            d.get(base + "/")
            assert wd_js(d, "return document.title") == "Portal"
            wd_click(d, "#go")
            wd_wait(d, "#t tbody tr")
            assert wd_tables(d, match="Amount")[0]["Id"].tolist() == ["1", "2"]
            wd_click(d, "#ask")
            assert wd_accept_alert(d) == "Sure?" and wd_text(d, "#ans") == "yes"
            wd_click(d, "#dl")
            p = wd_wait_download(chrome_kw["download_dir"], timeout_s=30)
            assert p.name == "x.csv" and p.read_text() == "a,b\n1,2\n"
            wd_click(d, "#pop")
            wd_window(d, -1)
            assert wd_text(d, "h1") == "Popup page"
            wd_window(d, 0)
            d.get(base + "/form")
            wd_select(d, "#ccy", "SGD")
            wd_select(d, "#ccy", "US Dollar")
            assert wd_js(d, "return document.querySelector('#ccy').value") == "USD"
            wd_fill(d, "#name", "abc")
            assert wd_js(d, "return document.querySelector('#name').value") == "abc"
            wd_frame(d, "#f")
            assert wd_text(d, "#in") == "inside frame"
            wd_frame(d)
            d.get(base + "/login")
            s = wd_requests_session(d)
            assert api_get(s, base + "/api/secure") == {"ok": True}
            assert wd_screenshot(d, tmp_path / "s.png").exists()
            with pytest.raises(SystemError_):
                wd_click(d, "#missing", timeout=1)
        with pytest.raises(ZeroDivisionError):
            with webdriver_session(screenshots_dir=tmp_path / "err", **chrome_kw) as d:
                d.get(base + "/")
                1 / 0
        assert list((tmp_path / "err").glob("error_*.png"))
