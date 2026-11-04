import smtplib
import threading
import time

import pandas as pd
import pytest

from toolbank import *


def square(x):
    if x == 3:
        raise ValueError("three")
    return x * x


def test_flow_trace_snapshots_and_failures(tmp_path):
    src = tmp_path / "in.csv"
    pd.DataFrame({"desk": ["FX", "EM", "FX"], "amt": [1, 2, 3]}).to_csv(src, index=False)
    logs = []
    f = (Flow("recon", keep=True, log=logs.append)
         .step("load", pd.read_csv, src)
         .step("fx only", lambda d: d[d["desk"] == "FX"])
         .check("has rows", lambda d: len(d) > 0)
         .step("double", lambda d, k: d.assign(amt=d["amt"] * k), 2))
    out = f.run()
    assert out["amt"].tolist() == [2, 6]
    assert f.trace[["step", "rows_in", "rows_out", "status"]].values.tolist() == [
        ["load", pd.NA, 3, "OK"], ["fx only", 3, 2, "OK"], ["has rows", 2, 2, "OK"], ["double", 2, 2, "OK"]]
    assert set(f.snapshots) == {"load", "fx only", "has rows", "double"} and len(logs) == 4
    bad = Flow("x", log=lambda m: None).step("empty", lambda d: d.iloc[0:0]).check("has rows", lambda d: len(d) > 0)
    with pytest.raises(RobotError) as e:
        bad.run(pd.DataFrame({"a": [1]}))
    assert e.value.code == "FLOW_CHECK" and bad.trace["status"].tolist() == ["OK", "FAILED"]
    biz = Flow("b", log=lambda m: None).step("validate", lambda d: (_ for _ in ()).throw(BusinessError("no", code="X")))
    with pytest.raises(BusinessError):
        biz.run(pd.DataFrame({"a": [1]}))
    with pytest.raises(TypeError):
        Flow("t", log=lambda m: None).step("oops", lambda d: len(d)).run(pd.DataFrame({"a": [1]}))


def test_count_rows_tee_cached(tmp_path):
    df = pd.DataFrame({"a": [1, 2]})
    msgs = []
    assert count_rows(df, "after", log=msgs.append) is df and msgs == ["after: 2 rows x 1 cols"]
    assert df.pipe(count_rows, log=msgs.append).equals(df)
    tee(df, tmp_path / "mid" / "x.csv")
    assert pd.read_csv(tmp_path / "mid" / "x.csv")["a"].tolist() == [1, 2]
    calls = []

    def slow():
        calls.append(1)
        return df

    for _ in range(2):
        assert cached(tmp_path / "c.pkl", slow)["a"].tolist() == [1, 2]
    assert len(calls) == 1
    cached(tmp_path / "c.pkl", slow, refresh=True)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        cached(tmp_path / "c.json", slow)


def test_build_save_read_eml(tmp_path):
    att = tmp_path / "report.csv"
    att.write_text("a,b\n1,2\n")
    msg = build_email("bot@bank.test", ["ops@bank.test", "lead@bank.test"], "Daily recon",
                      html="<p>Breaks: <b>3</b></p>", cc="cc@bank.test", attachments=[att])
    p = save_eml(msg, tmp_path / "out" / "draft.eml")
    raw = p.read_bytes()
    assert b"X-Unsent: 1\r\n" in raw and b"\r\n" in raw
    info = read_eml(p)
    assert info["subject"] == "Daily recon" and info["to"] == "ops@bank.test, lead@bank.test"
    assert info["text"].strip() == "Breaks: 3" and "<b>3</b>" in info["html"] and info["attachments"] == ["report.csv"]
    saved = eml_attachments(p, tmp_path / "atts", pattern=r"\.csv$")
    assert saved[0].read_text() == "a,b\n1,2\n"
    again = eml_attachments(p, tmp_path / "atts")
    assert again[0].name == "report_2.csv"                         # never overwrites
    df = read_eml_folder(tmp_path / "out")
    assert df.loc[0, "file"] == "draft.eml" and df.loc[0, "cc"] == "cc@bank.test"


def test_read_eml_html_only_and_nested(tmp_path):
    from email.message import EmailMessage
    inner = EmailMessage()
    inner["Subject"] = "inner"
    inner.set_content("hello")
    outer = EmailMessage()
    outer["Subject"] = "outer"
    outer["Date"] = "not a date"
    outer.add_alternative("<div>Only <i>html</i></div>", subtype="html")
    outer.add_attachment(inner)
    p = tmp_path / "o.eml"
    p.write_bytes(bytes(outer))
    info = read_eml(p)
    assert info["text"] == "Only html" and info["date"] is None
    saved = eml_attachments(p, tmp_path / "a")
    assert saved and saved[0].suffix == ".eml" and b"hello" in saved[0].read_bytes()


class FakeSMTP:
    sent, fail = [], None

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.fail == "connect":
            raise ConnectionRefusedError("no relay")
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        if FakeSMTP.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, msg):
        if FakeSMTP.fail == "refused":
            raise smtplib.SMTPRecipientsRefused({"x@y": (550, b"no such user")})
        FakeSMTP.sent.append(msg["Subject"])
        return {}


def test_smtp_send_classified():
    msg = build_email("a@x.test", "b@x.test", "Hi", text="body")
    assert smtp_send(msg, "relay", smtp_cls=FakeSMTP) == {} and FakeSMTP.sent == ["Hi"]
    assert smtp_send(msg, "relay", dry_run=True, smtp_cls=FakeSMTP) == {} and FakeSMTP.sent == ["Hi"]
    for fail, exc, code in [("refused", BusinessError, "SMTP_REFUSED"), ("connect", SystemError_, "SMTP")]:
        FakeSMTP.fail = fail
        with pytest.raises(exc) as e:
            smtp_send(msg, "relay", smtp_cls=FakeSMTP)
        assert e.value.code == code
    FakeSMTP.fail = "auth"
    with pytest.raises(RobotError) as e:
        smtp_send(msg, "relay", user="u", password="p", smtp_cls=FakeSMTP)
    assert e.value.code == "AUTH"
    FakeSMTP.fail = None


def test_parallel_map_threads_and_processes():
    out = parallel_map(square, [1, 2, 3, 4], workers=3)
    assert [o["result"] for o in out] == [1, 4, None, 16] and out[2]["error"] == "ValueError: three"
    seen = set()

    def slow(x):
        seen.add(threading.get_ident())
        time.sleep(0.05)
        return x

    t = time.perf_counter()
    assert [o["result"] for o in parallel_map(slow, range(8), workers=8)] == list(range(8))
    assert time.perf_counter() - t < 0.35 and len(seen) > 1
    out = parallel_map(square, [2, 3], workers=2, processes=True)
    assert out[0]["result"] == 4 and not out[1]["ok"]


def test_chunks_timer_progress_memory():
    assert list(chunks(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]
    msgs = []
    with timer("load", log=msgs.append) as t:
        pass
    assert "ms" in t and msgs[0].startswith("load: ")
    clock = iter(range(0, 1000, 1))
    lines = []
    assert list(progress(range(250), every=100, label="rows", log=lines.append, clock=lambda: next(clock))) == \
        list(range(250))
    assert len(lines) == 3 and lines[0].startswith("rows 100/250 (40%)") and lines[-1].startswith("rows 250/250 (100%)")
    df = pd.DataFrame({"n": range(1000), "desk": ["FX", "EM"] * 500, "id": [f"T{i}" for i in range(1000)],
                       "amt": [1.123456789] * 1000})
    small = shrink(df)
    assert str(small["n"].dtype) == "int16" and str(small["desk"].dtype) == "category"
    assert small["id"].dtype == df["id"].dtype and small["amt"].dtype == "float64"
    assert small.astype({"desk": df["desk"].dtype}).equals(df.astype({"n": "int16"}))
    assert memory_mb(small) < memory_mb(df)
