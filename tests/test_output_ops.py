import json
from datetime import date, datetime
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
    rec = json.loads((tmp_path / "run.jsonl").read_text().strip())
    assert rec["run_id"] == "R1" and rec["rows"] == 5 and "<account:***>" in rec["msg"]
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
    assert cfg == {"input_path": "uat.xlsx", "tolerance": "0.5", "env": "uat"}
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
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("robot_template", root / "templates" / "robot_template.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    pd.DataFrame({"id": ["A", "B"]}).to_excel(tmp_path / "in.xlsx", index=False)
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "default.yaml").write_text(f"input_path: {tmp_path / 'in.xlsx'}\nworkdir: {tmp_path / 'work'}\n")
    assert m.main(["--config-dir", str(tmp_path / "cfg"), "--business-date", "2026-03-06"]) == 0
    assert (tmp_path / "work" / "my_robot" / "2026-03-06" / "my_robot_report.xlsx").exists()
