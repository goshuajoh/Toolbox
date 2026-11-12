import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from toolbank import *

SG = ZoneInfo("Asia/Singapore")


def test_write_report_and_safe_write(tmp_path):
    df = pd.DataFrame({"Ref": ["A"], "amount": [1234.5]})
    p = write_report(tmp_path / "r.xlsx", {"Data": df}, number_formats={"amount": "#,##0.00"})
    ws = load_workbook(p)["Data"]
    assert ws["A1"].font.bold and ws.freeze_panes == "A2" and ws["B2"].number_format == "#,##0.00"
    with pytest.raises(FileExistsError):
        safe_write_excel(p, {"Data": df})
    safe_write_excel(p, {"Data": df}, overwrite=True)
    assert [f.name for f in tmp_path.iterdir()] == ["r.xlsx"]


def test_template_and_workbook_features(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title = "Data"
    ws["A1"], ws["B1"] = "Ref", "Amt"; ws["A1"].font = Font(bold=True); ws["D1"] = "=SUM(B:B)"
    for r in range(2, 10):
        ws.cell(r, 1).value = "old"
    wb.save(tmp_path / "tpl.xlsx")
    df = pd.DataFrame({"Ref": ["A1", "A2", "A3"], "Amt": [1.5, None, 3.0]})
    write_into_template(tmp_path / "tpl.xlsx", tmp_path / "out.xlsx", {"Data": df})
    w = load_workbook(tmp_path / "out.xlsx")["Data"]
    assert w["A2"].value == "A1" and w["A5"].value is None and w["B3"].value is None
    assert w["A1"].font.bold and w["D1"].value == "=SUM(B:B)"

    p = tmp_path / "rep.xlsx"
    df.to_excel(p, sheet_name="R", index=False)
    format_as_table(p, "R", "Rep")
    add_conditional_format(p, "R", "A2:B4", "$B2>1")
    add_dropdown(p, "R", "C2:C4", ["APPROVED", "REJECTED"])
    assert add_formula_column(p, "R", "Double", "=B{r}*2") == "C"
    add_totals_row(p, "R", ["Amt"])
    highlight_cells(p, "R", ["A2"])
    protect_sheet(p, "R", "pw", editable_cols=["C"])
    s = load_workbook(p)["R"]
    assert s["C2"].value == "=B2*2" and s["B5"].value == "=SUBTOTAL(109,B2:B4)"
    assert s.protection.sheet and not s["C3"].protection.locked and s["A3"].protection.locked
    assert "Rep" in s.tables and len(s.data_validations.dataValidation) == 1


def test_split_and_csv(tmp_path):
    paths = split_to_files(pd.DataFrame({"cp": ["A/B", "C"], "v": [1, 2]}), "cp", tmp_path / "s")
    assert sorted(p.name for p in paths) == ["A_B.xlsx", "C.xlsx"]
    assert len(split_to_files(pd.DataFrame({"cp": ["A", "C"], "v": [1, 2]}), ["cp"], tmp_path / "s2", "csv")) == 2
    to_csv_strict(pd.DataFrame({"d": [pd.Timestamp("2026-03-06")], "a": [1.5]}), tmp_path / "s.csv", quote_all=True)
    assert (tmp_path / "s.csv").read_bytes() == b'"d","a"\r\n"2026-03-06","1.50"\r\n'


def test_robust(tmp_path):
    assert isinstance(classify(PermissionError("x")), SystemError_) and isinstance(classify(KeyError("k")), BusinessError)
    calls = {"n": 0}

    @retry(max_attempts=3, base_delay=0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return "ok"
    assert flaky() == "ok" and calls["n"] == 3

    @retry(max_attempts=3, base_delay=0)
    def bad():
        calls["n"] += 1
        raise BusinessError("no")
    calls["n"] = 0
    with pytest.raises(BusinessError):
        bad()
    assert calls["n"] == 1

    def handler(x):
        if x == 2:
            raise BusinessError("bad row", code="BAD")
    cp = Checkpoint(tmp_path / "cp.json")
    done, fails = process_all([1, 2, 3], handler, checkpoint=cp)
    assert done == [1, 3] and fails["code"].tolist() == ["BAD"]
    done2, _ = process_all([1, 2, 3, 4], handler, checkpoint=Checkpoint(tmp_path / "cp.json"))
    assert done2 == [4]

    def down(x):
        raise OSError("network")
    with pytest.raises(SystemError_, match="consecutive"):
        process_all([1, 2, 3, 4], down)
    assert act(lambda: 1 / 0, dry_run=True) is None and act(lambda a: a + 1, 1) == 2
    lines = []
    with step("s", lines.append):
        pass
    assert lines[0] == "START s" and lines[1].startswith("DONE")
    assert run_dir("r", "2026-03-06", root=tmp_path).is_dir()


def test_logs_and_privacy(tmp_path):
    assert redact("acct 0012345678 joe@bank.com S1234567A card 4111 1111 1111 1111") == \
        "acct <account:***> <email:***> <nric:***> card <account:***>"
    assert redact("ref GL-00012345 run 20261102-173536 date 2026-03-06") == "ref GL-00012345 run 20261102-173536 date 2026-03-06"
    log = get_logger(tmp_path / "run.jsonl", run_id="R1", console=False)
    log.info("loaded 0012345678", extra={"rows": 5, "event": "load"})
    log.info("ctx", extra={"custom_key": "S1234567A", "n": {"a": 1, "acct": "0012345678"}, "ids": ["S1234567A"],
                           "level": "high", "run_id": "sub", "nan": float("nan"), "np": np.int64(5), "flag": np.bool_(True),
                           "p": Path("/x/S1234567A.xlsx")})
    try:
        1 / 0
    except ZeroDivisionError:
        log.error("boom", exc_info=True, extra={"code": "X"})
    rec, ctx, err = [json.loads(ln) for ln in (tmp_path / "run.jsonl").read_text().strip().splitlines()]
    assert rec["run_id"] == "R1" and rec["rows"] == 5 and "<account:***>" in rec["msg"]
    assert ctx["custom_key"] == "<nric:***>" and ctx["n"] == {"a": 1, "acct": "<account:***>"}
    assert ctx["ids"] == ["<nric:***>"] and ctx["level"] == "INFO" and ctx["run_id"] == "R1" and ctx["nan"] is None
    assert ctx["np"] == 5 and ctx["flag"] is True and ctx["p"] == "/x/<nric:***>.xlsx"
    assert err["code"] == "X" and err["exc"].rstrip().endswith("ZeroDivisionError: division by zero")
    assert redact_frame(pd.DataFrame({"n": ["Joe", None]}), ["n"])["n"].tolist()[0] == "***"
    ds = describe_shape(pd.DataFrame({"ref": ["GL-000001", "GL-000002"], "amt": [123.0, 4567.0], "ok": [True, False],
                                      "d": pd.to_datetime(["2026-01-01", "2026-01-02"])}))
    assert ds.set_index("column").loc["ref", "shape"] == "AA-999999" and "GL-000001" not in ds.to_string()
    fk = synth(10, {"ref": ("pattern", "GL-999999"), "amt": ("number", -10, 10), "d": ("date", "2026-01-01", "2026-01-31"),
                    "c": ("choice", ["SGD"]), "i": ("int", 1, 3), "id": ("seq", 100)})
    assert len(fk) == 10 and fk["ref"].str.match(r"GL-\d{6}").all() and fk["id"].tolist()[0] == 100
    assert synth(3, {"a": ("number", 0, 1)}, seed=1).equals(synth(3, {"a": ("number", 0, 1)}, seed=1))


def test_dates():
    hol = {date(2026, 3, 9)}
    assert add_business_days(date(2026, 3, 6), 2, hol) == date(2026, 3, 11)
    assert add_business_days(date(2026, 3, 11), -2, hol) == date(2026, 3, 6)
    assert next_business_day(date(2026, 3, 7), hol) == date(2026, 3, 10)
    assert next_business_day(date(2026, 3, 10), hol, inclusive=True) == date(2026, 3, 10)
    assert business_days_between(date(2026, 3, 6), date(2026, 3, 11), hol) == 2
    assert business_days_between(date(2026, 3, 11), date(2026, 3, 6), hol) == -2
    assert month_end(date(2026, 2, 10)) == date(2026, 2, 28) and month_end(date(2026, 2, 10), business=True) == date(2026, 2, 27)
    assert assert_before_cutoff("17:30", now=datetime(2026, 3, 6, 17, 0, tzinfo=SG)) == 30
    with pytest.raises(RobotError, match="PAST_CUTOFF|past cut-off"):
        assert_before_cutoff("17:30", now=datetime(2026, 3, 6, 9, 31, tzinfo=ZoneInfo("Europe/London")))


def test_load_holidays(tmp_path):
    pd.DataFrame({"date": ["2026-01-01", "17/02/2026"]}).to_csv(tmp_path / "h.csv", index=False)
    assert load_holidays(tmp_path / "h.csv") == {date(2026, 1, 1), date(2026, 2, 17)}


def test_files(tmp_path):
    f = tmp_path / "in.csv"; f.write_text("a,b\n1,2\n")
    assert wait_for_file(tmp_path / "*.csv", timeout_s=5, poll_s=0.2, stable_s=0.3) == f
    with pytest.raises(SystemError_):
        wait_for_file(tmp_path / "*.xlsx", timeout_s=0.5, poll_s=0.1)
    assert newest_file(tmp_path / "*.csv") == f and newest_file(tmp_path / "*.zz") is None
    assert not is_locked(f)
    assert len(file_sha256(f)) == 64
    assert safe_filename('a/b:c*?"<>|') == "a_b_c_"
    z = zip_files([f], tmp_path / "z" / "x.zip")
    assert [p.name for p in unzip(z, tmp_path / "out")] == ["in.csv"]
    a = archive(f, tmp_path / "arch")
    assert a.exists() and not f.exists() and archive(a, tmp_path / "arch2").exists()


def test_approval_roundtrip(tmp_path):
    p = tmp_path / "appr.xlsx"
    propose(p, [{"action_id": "T1", "action": "AFFIRM", "qty": 100, "price": 101.25, "settle": date(2026, 3, 10)},
                {"action_id": "T2", "action": "AFFIRM", "qty": 200, "price": 99.0, "settle": date(2026, 3, 10)},
                {"action_id": "T3", "action": "AFFIRM", "qty": 300, "price": 50.0, "settle": date(2026, 3, 10)},
                {"action_id": "T4", "action": "AFFIRM", "qty": 400, "price": 1.0, "settle": date(2026, 3, 10)}],
            protect_password="pw")
    ok, rest = approved_actions(p)
    assert ok == [] and rest == ["T1", "T2", "T3", "T4"]          # nothing approved yet; ints/dates did not false-tamper
    wb = load_workbook(p); ws = wb["Approvals"]
    hdr = {c.value: c.column for c in ws[1]}
    ws.cell(2, hdr["decision"]).value, ws.cell(2, hdr["checker"]).value = "APPROVED", "chk01"     # T1 ok
    ws.cell(3, hdr["decision"]).value = "APPROVED"                                                # T2 no checker id
    ws.cell(4, hdr["decision"]).value, ws.cell(4, hdr["checker"]).value = "APPROVED", "chk01"
    ws.cell(4, hdr["proposed_price"]).value = 5.0                                                 # T3 tampered
    ws.cell(5, hdr["decision"]).value, ws.cell(5, hdr["checker"]).value = "REJECTED", "chk01"     # T4 rejected
    wb.save(p)
    d = read_decisions(p).set_index("action_id")["why"].to_dict()
    assert d == {"T1": "OK", "T2": "NO_CHECKER_ID", "T3": "TAMPERED", "T4": "NOT_APPROVED"}
    assert approved_actions(p)[0] == ["T1"]
    assert load_workbook(p)["Approvals"].protection.sheet


def test_docs(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document(); d.add_paragraph("Dear {{name}}, {{n}} breaks."); d.sections[0].header.paragraphs[0].text = "{{date}}"
    t = d.add_table(rows=2, cols=2); t.rows[0].cells[0].text = "a"; t.rows[0].cells[1].text = "b"
    t.rows[1].cells[0].text = "1"; t.rows[1].cells[1].text = "{{n}}"
    d.save(tmp_path / "t.docx")
    with pytest.raises(ValueError, match="unfilled"):
        fill_docx(tmp_path / "t.docx", tmp_path / "o.docx", {"name": "Ops"})
    fill_docx(tmp_path / "t.docx", tmp_path / "o.docx", {"name": "Ops", "n": 3, "date": "6 Mar"})
    assert "Dear Ops, 3 breaks." in docx_text(tmp_path / "o.docx")
    assert docx_tables(tmp_path / "o.docx")[0].to_dict("records") == [{"a": "1", "b": "3"}]
    df_to_docx_table(tmp_path / "o.docx", pd.DataFrame({"x": [1, None]}), heading="Results")
    assert len(docx_tables(tmp_path / "o.docx")) == 2


def test_pdf(tmp_path):
    pytest.importorskip("pdfplumber")
    rl = pytest.importorskip("reportlab.pdfgen.canvas")
    c = rl.Canvas(str(tmp_path / "c.pdf")); c.drawString(72, 720, "Trade TRD-00012345 amount USD 1,200.50"); c.save()
    assert "TRD-00012345" in pdf_text(tmp_path / "c.pdf")
    assert pdf_find(tmp_path / "c.pdf", r"TRD-\d{8}") == ["TRD-00012345"]
    assert pdf_find(tmp_path / "c.pdf", r"(?P<ccy>[A-Z]{3}) (?P<amt>[\d,.]+)") == [{"ccy": "USD", "amt": "1,200.50"}]


def test_config_and_notify(tmp_path, monkeypatch):
    (tmp_path / "default.yaml").write_text("input_path: a.xlsx\ntolerance: 0.01\n")
    (tmp_path / "uat.yaml").write_text("input_path: uat.xlsx\n")
    monkeypatch.setenv("BOT_TOLERANCE", "0.5")
    cfg = load_config(tmp_path, "uat")
    assert cfg == {"input_path": "uat.xlsx", "tolerance": 0.5, "env": "uat"}
    with pytest.raises(KeyError):
        require(cfg, "missing_key")
    monkeypatch.setenv("MY_SECRET", "s3")
    assert get_secret("MY_SECRET") == "s3"
    sent = []
    teams_message("https://hook", "Robot PARTIAL", {"Exceptions": 3}, post=lambda u, json: sent.append(json) or 200)
    assert sent[0]["sections"][0]["facts"] == [{"name": "Exceptions", "value": "3"}]
    with pytest.raises(RuntimeError):
        teams_message("https://hook", "x", post=lambda u, json: 500)


def test_template_robot_runs(tmp_path):
    m = _template_module()
    pd.DataFrame({"id": ["A", "B"]}).to_excel(tmp_path / "in.xlsx", index=False)
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "default.yaml").write_text(f"input_path: {tmp_path / 'in.xlsx'}\nworkdir: {tmp_path / 'work'}\n")
    (tmp_path / "cfg" / "dev.yaml").write_text("")
    assert m.main(["--config-dir", str(tmp_path / "cfg"), "--business-date", "2026-03-06"]) == 0
    assert (tmp_path / "work" / "my_robot" / "2026-03-06" / "my_robot_report.xlsx").exists()


def test_process_all_failure_details_and_stop():
    def handler(x):
        if x == 2:
            raise BusinessError("bad row", code="BAD")
        if x == 5:
            raise AttributeError("'NoneType' object has no attribute 'x'")
    lines = []
    done, fails = process_all([1, 2, 3], handler, log=lines.append)
    assert done == [1, 3] and fails.columns.tolist() == ["item", "code", "message", "type", "where", "retryable"]
    assert fails.loc[0, "type"] == "BusinessError" and fails.loc[0, "where"].startswith("handler (test_output_ops.py:")
    assert lines == [f"skip 2: [BAD] bad row at {fails.loc[0, 'where']}"]
    with pytest.raises(RobotError) as e:                     # a code bug stops at once, with the item named
        process_all([4, 5, 6], handler, log=lines.append)
    assert e.value.context["item"] == "5" and isinstance(e.value.__cause__, AttributeError)
    assert not isinstance(e.value, SystemError_) and lines[-1].startswith("stop at 5: [ERROR]")

    def auth(x):
        raise RobotError("HTTP 401", code="AUTH", url="x")
    with pytest.raises(RobotError, match="HTTP 401") as e:
        process_all([1, 2], auth)
    assert e.value.context["url"] == "x" and e.value.context["item"] == "1"
    assert e.value.context["where"].startswith("auth (test_output_ops.py:")

    def down(x):
        raise OSError("network down")
    with pytest.raises(SystemError_, match="3 consecutive system failures; last: OSError: network down") as e:
        process_all([1, 2, 3, 4], down)
    assert e.value.context["last_item"] == "3" and e.value.context["where"].startswith("down (test_output_ops.py:")
    info = error_info(e.value)
    assert info["code"] == "SYSTEM_STREAK" and info["retryable"] and info["where"].startswith("test_process_all")
    assert error_info(ValueError("x")) == {"type": "ValueError", "code": "BAD_DATA", "message": "x",
                                           "retryable": False, "where": "", "context": {}}


def test_retry_step_act_logging():
    lines, calls = [], {"n": 0}

    @retry(max_attempts=3, base_delay=0, log=lines.append)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return "ok"
    assert flaky() == "ok" and len(lines) == 2
    assert lines[-1].startswith("flaky: retry 2/2 in 0.0s after PermissionError: locked")
    with pytest.raises(KeyError):
        with step("load", lines.append):
            pd.DataFrame({"a": [1]})["b"]
    assert lines[-1].startswith("FAIL  load (") and "[BAD_DATA] 'b' at test_retry_step_act_logging" in lines[-1]
    assert act(lambda: 1 / 0, dry_run=True, log=lines.append) is None
    assert lines[-1] == "[dry-run] would call <lambda>(0 positional args, kwargs=[])"


def test_config_layers(tmp_path, monkeypatch):
    (tmp_path / "default.yaml").write_text("tolerance: 0.01\ndb:\n  host: h1\n  port: 1433\nids: ['0123']\n")
    (tmp_path / "uat.yaml").write_text("db:\n  host: h2\n")
    for k, v in {"BOT_TOLERANCE": "0.5", "BOT_DB__PORT": "1434", "BOT_ACCT": "0123", "BOT_SEND": "false",
                 "BOT_NAME": " x y ", "BOT_COUNTRY": "NO", "BOT_": "9"}.items():
        monkeypatch.setenv(k, v)
    assert load_config(tmp_path, "uat") == {"tolerance": 0.5, "db": {"host": "h2", "port": 1434}, "ids": ["0123"],
                                            "acct": "0123", "send": False, "name": "x y", "country": "NO", "env": "uat"}
    (tmp_path / "ids.yaml").write_text("branch: 00123\nsheet: 01\ncountry: NO\nflag: on\nok: true\nn: 12\nx: 0x1A\n")
    assert load_config(tmp_path, "ids") == {"tolerance": 0.5, "db": {"host": "h1", "port": 1434}, "ids": ["0123"],
                                            "branch": "00123", "sheet": "01", "country": "NO", "flag": "on", "ok": True,
                                            "n": 12, "x": 26, "acct": "0123", "send": False, "name": "x y", "env": "ids"}
    (tmp_path / "bad.yaml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        load_config(tmp_path, "bad")


def test_process_all_records_system_failures_and_keys(tmp_path):
    calls = {"n": 0}

    def h(x):
        if x == 2 and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError("file locked")
        if x == 3:
            raise BusinessError("bad", code="BAD")
    cp = Checkpoint(tmp_path / "cp.json")
    done, fails = process_all([1, 2, 3, 3, None, 4], h, checkpoint=cp, key=lambda x: x)
    assert done == [1, 4] and sorted(cp.done) == ["1", "4"]            # failures are never checkpointed
    assert fails["code"].tolist() == ["SYSTEM", "BAD", "DUPLICATE_KEY", "MISSING_KEY"]
    assert fails["retryable"].tolist() == [True, False, False, False] and fails["item"].tolist()[:3] == [2, 3, 3]
    done2, fails2 = process_all([1, 2, 3, 4], h, checkpoint=cp, key=lambda x: x)      # re-run: 2 now works, 3 fails again
    assert done2 == [2] and fails2["code"].tolist() == ["BAD"] and sorted(cp.done) == ["1", "2", "4"]

    def bug(row):
        return row["id"] + 1
    with pytest.raises(RobotError) as e:                                   # TypeError is a bug: stop at once
        process_all([{"id": "A1"}, {"id": "A2"}], bug, key=lambda r: r["id"])
    assert not isinstance(e.value, BusinessError) and e.value.context["item"] == "A1"
    assert e.value.context["where"].startswith("bug (test_output_ops.py:")

    def robot_mkdir():
        import os
        os.makedirs("/proc/nope/x")
    try:
        robot_mkdir()
    except Exception as exc:
        assert error_where(exc).startswith("robot_mkdir (test_output_ops.py:")   # not '<frozen os>'
    with pytest.raises(ValueError, match="max_attempts"):
        retry(max_attempts=0)
    import functools
    msgs = []
    act(functools.partial(print, "x"), dry_run=True, log=msgs.append)
    assert msgs == ["[dry-run] would call print(0 positional args, kwargs=[])"]


def _template_module():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("robot_template", root / "templates" / "robot_template.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_template_exit_codes(tmp_path, monkeypatch):
    m = _template_module()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    pd.DataFrame({"id": ["A", "B"]}).to_excel(tmp_path / "in.xlsx", index=False)
    pd.DataFrame({"ref": ["A"]}).to_excel(tmp_path / "noid.xlsx", index=False)
    (cfg / "default.yaml").write_text(f"input_path: {tmp_path / 'in.xlsx'}\nworkdir: {tmp_path / 'work'}\n")
    (cfg / "prod.yaml").write_text(f"input_path: {tmp_path / 'noid.xlsx'}\nworkdir: {tmp_path / 'work'}\n")
    (cfg / "gone.yaml").write_text(f"input_path: {tmp_path / 'nope.xlsx'}\nworkdir: {tmp_path / 'work'}\n")
    args = ["--config-dir", str(cfg), "--business-date", "2026-03-06"]
    assert m.main(args) == 4                                               # no dev.yaml: config error, not a live run
    (cfg / "dev.yaml").write_text("")
    with pytest.raises(SystemExit):                                        # only YYYY-MM-DD: one folder per date
        m.main(["--config-dir", str(cfg), "--business-date", "06/03/2026"])
    day = tmp_path / "work" / "my_robot" / "2026-03-06"
    assert m.main(args + ["--dry-run"]) == 0
    lines = [json.loads(ln) for ln in (day / "run.log.jsonl").read_text().splitlines()]
    assert lines[0]["dry_run"] is True and (day / "my_robot_report_dryrun.xlsx").exists()
    assert not (day / "my_robot_report.xlsx").exists() and not (day / "checkpoint.json").exists()   # live run still does all
    assert m.main(args) == 0 and sorted(Checkpoint(day / "checkpoint.json").done) == ["A", "B"]
    (day / "checkpoint.json").unlink()
    log_file = day / "run.log.jsonl"
    monkeypatch.setenv("BOT_ENV", "prod")                                  # BOT_ENV is honoured by --env's default
    assert m.main(args) == 2                                               # missing required column -> owner
    last = json.loads(log_file.read_text().splitlines()[-1])
    assert last["code"] == "BAD_DATA" and "exc" in last and last["type"] == "MissingColumns"
    monkeypatch.delenv("BOT_ENV")
    assert m.main(args + ["--env", "gone"]) == 3                           # input file missing -> re-run
    m.cleanse = lambda df: None.x
    assert m.main(args) == 5                                               # a bug -> needs a fix
    del m.cleanse
    with single_instance("my_robot", lock_dir=tmp_path / "work" / "my_robot" / "2026-03-06"):
        assert m.main(args) == 6
    (cfg / "dev.yaml").write_text(f"workdir: {tmp_path / 'in.xlsx'}\n")              # work folder is a file
    assert m.main(args) == 3


class _FakeTime:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_attempt_retry_fallback_give_up():
    t, log = _FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("element not found")
        return "ok"
    assert attempt(flaky, tries=3, wait_s=1, backoff=2, verify_timeout_s=0, **kw) == "ok"
    assert [s for s in t.slept if s >= 1] == [1, 2]                    # backoff between tries
    recovered, shots = [], []

    def boom():
        raise RuntimeError("down")

    def bad_recover():
        recovered.append(1)
        raise OSError("esc failed")
    with pytest.raises(SystemError_) as e:
        attempt(boom, tries=2, fallbacks=boom, recover=bad_recover, on_give_up=lambda: shots.append(1) or 1 / 0,
                name="Save", **kw)
    assert e.value.code == "STEP_FAILED" and e.value.context["step"] == "Save" and len(recovered) == 2 and shots
    assert [h["route"] for h in e.value.context["history"]] == ["primary", "primary", "fallback 1"]
    assert "Save: recover failed: OSError: esc failed" in log and any("on_give_up failed" in m for m in log)
    seen = {"v": 0}

    def verify():
        seen["v"] += 1
        if seen["v"] < 3:
            raise LookupError("not on screen yet")                    # an error while checking = not yet
        return True
    assert attempt(lambda: "x", verify=verify, verify_timeout_s=5, poll_s=1, **kw) == "x"


def test_attempt_stops_and_never_repeats():
    t, log = _FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    for exc in (BusinessError("closed", code="CLOSED"), RobotError("401", code="AUTH")):
        calls = []

        def fail(exc=exc, calls=calls):
            calls.append(1)
            raise exc
        with pytest.raises(type(exc)):
            attempt(fail, tries=3, **kw)
        assert len(calls) == 1                                         # data / access problems are not retried
    sent = []
    assert attempt(lambda: sent.append(1), already_done=lambda: True, **kw) is None and sent == []
    with pytest.raises(ValueError):
        attempt(lambda: 1, tries=0)
    with pytest.raises(ValueError):
        attempt(lambda: 1, tries=True)
    assert set(RETRY_PROFILES) == {"ui", "page_load", "file_lock", "read", "submit", "login"}
    assert RETRY_PROFILES["submit"]["tries"] == 1 and RETRY_PROFILES["login"]["tries"] == 1


def test_shared_fixes_from_pattern_review(tmp_path):
    p = tmp_path / "appr.xlsx"                                     # zero-padded text survives the Excel round trip
    propose(p, [{"action_id": "T1", "action": "PAY", "acct": "00123", "amt": 5.0}])
    wb = load_workbook(p); ws = wb["Approvals"]
    hdr = {c.value: c.column for c in ws[1]}
    ws.cell(2, hdr["decision"]).value, ws.cell(2, hdr["checker"]).value = "APPROVED", "chk01"
    wb.save(p)
    assert approved_actions(p) == (["T1"], [])
    missing = tmp_path / "gone.csv"
    assert is_locked(missing) is False and not missing.exists()        # checking never creates the file
    cp = Checkpoint(tmp_path / "cp.json")
    cp.mark_done("A"); cp.mark_done("B"); cp.discard("A"); cp.discard("never")
    assert sorted(Checkpoint(tmp_path / "cp.json").done) == ["B"]
    msg = build_email("a@x.test", "b@x.test", "s", text="t")
    save_eml(msg, tmp_path / "d" / "m.eml")
    assert [f.name for f in (tmp_path / "d").iterdir()] == ["m.eml"]  # no temp file left behind


def test_attempt_never_repeats_blind():
    t, log = _FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    sent = []

    def send():
        sent.append(1)
        raise TimeoutError("no reply")

    def lookup_down():
        if sent:
            raise ConnectionError("target down")
        return False
    with pytest.raises(SystemError_) as e:                     # cannot tell if it arrived: stop, do not resend
        attempt(send, already_done=lookup_down, **dict(RETRY_PROFILES["submit"], tries=2), **kw)
    assert e.value.code == "DONE_CHECK_FAILED" and sent == [1]
    with pytest.raises(ValueError, match="already_done"):     # irreversible: no second route without a check
        attempt(send, fallbacks=send, **RETRY_PROFILES["submit"], **kw)
    sent.clear()
    with pytest.raises(RobotError) as e:                      # unconfirmed send: a person checks, never re-run
        attempt(lambda: sent.append(1), verify=lambda: False, **RETRY_PROFILES["submit"], **kw)
    assert e.value.code == "STEP_UNCONFIRMED" and not e.value.retryable and sent == [1]
    with pytest.raises(RobotError) as e:
        process_all(["PAY1", "PAY2"], lambda i: attempt(lambda: sent.append(i), verify=lambda: False,
                                                        **RETRY_PROFILES["submit"], **kw))
    assert e.value.code == "STEP_UNCONFIRMED" and sent == [1, "PAY1"]   # the run stops at the first one
    with pytest.raises(TypeError, match="already_done must be a function"):
        attempt(lambda: 1, already_done=True)
    with pytest.raises(ValueError, match="poll_s"):
        attempt(lambda: 1, poll_s=0)


def test_attempt_bugs_data_frames_and_redaction():
    t, log = _FakeTime(), []
    kw = dict(log=log.append, sleep=t.sleep, clock=t.clock)
    calls = []

    def bug():
        calls.append(1)
        return None.click()
    with pytest.raises(AttributeError):
        attempt(bug, **RETRY_PROFILES["ui"], **kw)
    assert calls == [1]                                        # a bug is not retried
    with pytest.raises(BusinessError) as e:
        attempt(lambda: {}["amount"], **RETRY_PROFILES["ui"], **kw)
    assert e.value.code == "BAD_DATA"
    rows = iter([pd.DataFrame(), pd.DataFrame({"ref": ["PAY1"]})])
    assert attempt(lambda: "ok", verify=lambda: next(rows), verify_timeout_s=5, poll_s=1, **kw) == "ok"

    def leaky():
        raise RuntimeError("locator('text=jane.tan@example.com 4111 1111 1111 1111') not found")
    with pytest.raises(SystemError_):
        attempt(leaky, tries=1, **kw)
    assert "jane.tan@example.com" not in " ".join(log) and "<email:***>" in " ".join(log)


def test_approval_four_eyes_and_round_trip(tmp_path):
    import decimal
    p = tmp_path / "a.xlsx"
    odd = {"action_id": 1001, "action": "PAY", "amt": decimal.Decimal("101.50"), "na": pd.NA, "z": -0.0,
           "ts": pd.Timestamp("2026-03-06 10:30:15.123456"), "txt": "a\r\nb", "acct": "00123"}
    propose(p, [odd, {"action_id": "PAY-2", "action": "HOLD", "amt": 5000}, {"action_id": "PAY-3", "action": "PAY", "amt": 1}])
    wb = load_workbook(p); ws = wb["Approvals"]
    hdr = {c.value: c.column for c in ws[1]}
    for row, checker in ((2, "ben"), (3, "ben"), (4, "svc_robot")):
        ws.cell(row, hdr["decision"]).value, ws.cell(row, hdr["checker"]).value = "APPROVED", checker
    ws.cell(3, hdr["action"]).value = "PAY"                                  # HOLD changed to PAY
    wb.save(p)
    why = read_decisions(p, maker="svc_robot", approvers=["ben", "ann"]).set_index("action_id")["why"].to_dict()
    assert why == {"1001": "OK", "PAY-2": "TAMPERED", "PAY-3": "SAME_PERSON"}   # odd values survive; ids are text
    assert approved_actions(p, approvers=["ann"]) == ([], ["1001", "PAY-2", "PAY-3"])
    ws.append([ws.cell(2, c).value for c in range(1, ws.max_column + 1)])    # a copied row
    wb.save(p)
    assert read_decisions(p)["why"].tolist().count("DUPLICATE") == 2


def test_shared_fixes_second_review(tmp_path):
    from unittest import mock
    f = tmp_path / "x.csv"
    f.write_text("a")
    with mock.patch("os.open", side_effect=PermissionError(13, "denied")):
        with pytest.raises(RobotError) as e:
            is_locked(f)
    assert e.value.code == "NO_ACCESS"
    sharing = PermissionError(13, "sharing violation")
    sharing.winerror = 32
    with mock.patch("os.open", side_effect=sharing):
        assert is_locked(f) is True
    with pytest.raises(ValueError, match="folder"):
        is_locked(tmp_path)
    log = get_logger(tmp_path / "r.jsonl", run_id="R", console=False)
    log.info("x", extra={"item_id": 40012345678, "rows": 5})
    rec = json.loads((tmp_path / "r.jsonl").read_text())
    assert rec["item_id"] == "<account:***>" and rec["rows"] == 5
    shape = describe_shape(pd.DataFrame({"acct": [40012345678, 40098765432], "bal": [1234567.89, -98.12]}))
    assert shape["shape"].tolist() == ["up to 11 digits", "up to 7 digits, decimals, negatives"]
    with pytest.raises(BusinessError) as e:                    # a Monday re-run of Friday's date is late
        assert_before_cutoff("17:30", now=datetime(2026, 11, 30, 9, 0, tzinfo=SG), business_date="2026-11-27")
    assert e.value.code == "PAST_CUTOFF"
    assert assert_before_cutoff("17:30", now=datetime(2026, 11, 27, 17, 0, tzinfo=SG), business_date="2026-11-27") == 30
    with pytest.raises(KeyError) as e:
        get_secret("TOOLBANK_TEST_NO_SUCH_SECRET")
    assert isinstance(e.value, RobotError) and classify(e.value).code == "NO_SECRET"
