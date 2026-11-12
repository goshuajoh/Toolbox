from datetime import date

import numpy as np
import pandas as pd
import pytest

from toolbank import *

TRADES = pd.DataFrame({
    "id": ["T1", "T2", "T3", "T4", "T5"],
    "ccy": ["usd", "SGD", "EUR", None, "USD "],
    "amount": [1_500_000.0, 200.0, np.nan, 50.0, 9_000_000.0],
    "desk": ["FX", "RATES", "FX", "EM", "FX"],
    "trade_date": pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"]),
    "urgent": ["Y", "n", "", None, "yes"],
})


# ---------- rules ----------

def test_truthy_and_feature_flag(monkeypatch):
    assert [truthy(v) for v in ["Y", "yes", "TRUE", 1, "x", "N", "0", "", None, np.nan, True]] == \
        [True, True, True, True, True, False, False, False, False, False, True]
    assert feature_flag("send_email", {"send_email": "Y"}) is True
    monkeypatch.setenv("FLAG_SEND_EMAIL", "0")
    assert feature_flag("send_email", {"send_email": "Y"}) is False
    assert feature_flag("other", default=True) is True


def test_condition_operators():
    c = lambda col, op, v=None: condition(TRADES, col, op, v).tolist()     # noqa: E731
    assert c("ccy", "==", "USD") == [True, False, False, False, True]      # case/space-insensitive
    assert c("ccy", "in", "usd, sgd") == [True, True, False, False, True]
    assert c("ccy", "not in", "USD") == [False, True, True, False, False]  # blanks never match
    assert c("amount", ">", "1,000,000") == [True, False, False, False, True]
    assert c("amount", "between", "100, 1000") == [False, True, False, False, False]
    assert c("amount", "is null") == [False, False, True, False, False]
    assert c("ccy", "is blank") == [False, False, False, True, False]
    assert c("desk", "startswith", "f") == [True, False, True, False, True]
    assert c("desk", "regex", "^(FX|EM)$") == [True, False, True, True, True]
    assert c("desk", "not contains", "X") == [False, True, False, True, False]
    assert c("trade_date", ">=", "05/03/2026") == [False, False, False, True, True]
    assert c("urgent", "is true") == [True, False, False, False, True]
    assert c("urgent", "is false") == [False, True, False, False, False]      # blanks never match
    assert c("desk", "!=", "fx") == [False, True, False, True, False]
    assert condition(TRADES, "desk", "==", "fx", case=True).tolist() == [False] * 5
    with pytest.raises(KeyError):
        condition(TRADES, "nope", "==", 1)
    with pytest.raises(ValueError):
        condition(TRADES, "desk", "~~", 1)


def test_apply_rules_from_excel(tmp_path):
    rules = pd.DataFrame([
        {"code": "BIG_USD", "column": "ccy", "op": "eq", "value": "USD", "active": "Y"},
        {"code": "BIG_USD", "column": "amount", "op": "greater than", "value": "1000000", "active": "Y"},
        {"code": "NO_AMT", "column": "amount", "op": "is blank", "value": None, "active": "Y"},
        {"code": "OFF", "column": "desk", "op": "==", "value": "FX", "active": "N"},
    ])
    path = tmp_path / "rules.xlsx"
    rules.to_excel(path, index=False)
    r = rules_from_excel(path)
    assert sorted(r["code"].unique()) == ["BIG_USD", "NO_AMT"]
    out = apply_rules(TRADES, r)
    assert out["flags"].tolist() == ["BIG_USD", "", "NO_AMT", "", "BIG_USD"]
    assert out["flags_count"].tolist() == [1, 0, 1, 0, 1] and out["flag_BIG_USD"].dtype == bool
    assert flag_summary(out).values.tolist() == [["BIG_USD", 2], ["NO_AMT", 1]]
    probs = validate_rules([{"code": "X", "column": "zzz", "op": "≈"}], TRADES)
    assert probs[0].startswith("X: unknown operator '≈'") and probs[1] == "X: column 'zzz' not in data"
    bad = tmp_path / "bad.xlsx"
    pd.DataFrame([{"code": "X", "column": "desk", "op": "approx", "value": 1}]).to_excel(bad, index=False)
    with pytest.raises(ValueError):
        rules_from_excel(bad)
    formula_op = tmp_path / "formula.xlsx"
    pd.DataFrame([{"code": "X", "column": "desk", "op": "==", "value": "FX"}]).to_excel(formula_op, index=False)
    with pytest.raises(ValueError, match="formula"):
        rules_from_excel(formula_op)
    route = first_match(TRADES, [("ESCALATE", [("amount", ">", 5e6)]), ("REVIEW", [("desk", "==", "FX")])],
                        default="AUTO")
    assert route.tolist() == ["REVIEW", "AUTO", "REVIEW", "AUTO", "ESCALATE"]


# ---------- formula ----------

def test_rounding_and_basic_formulas():
    assert round_half_up(2.5) == 3 and round_half_up(-2.5) == -3 and round_half_up(0.125, 2) == 0.13
    assert round_half_up(pd.Series([1.005, 2.675, np.nan]), 2).tolist()[:2] == [1.01, 2.68]
    assert roundup(1.21, 1) == 1.3 and rounddown(-1.29, 1) == -1.2
    assert ceiling(-1.25, 1) == -1.2 and floor(-1.21, 1) == -1.3
    assert iferror(pd.Series(["1", "x"]), int, default=-1).tolist() == [1, -1]
    a, b = pd.Series([None, "", "x", None]), pd.Series(["p", "q", "r", None])
    assert coalesce(a, b).tolist()[:3] == ["p", "q", "x"] and pd.isna(coalesce(a, b).iloc[3])
    df = pd.DataFrame({"a": ["x", None], "b": ["y", "z"]})
    assert textjoin(df, ["a", "b"], "-").tolist() == ["x-y", "z"]
    assert safe_divide(pd.Series([1.0, 2.0, 3.0]), pd.Series([2.0, 0.0, np.nan]), default=0).tolist() == [0.5, 0, 0]
    assert rank(pd.Series([10, 30, 30, 5])).tolist() == [3, 1, 1, 4]
    assert sumproduct(pd.Series([1, 2]), pd.Series([3, 4])) == 11.0
    assert sign(pd.Series([-2, 0, 3])).tolist() == [-1, 0, 1]
    s = pd.Series(["ABCDEF", None])
    assert left(s, 2).tolist()[0] == "AB" and right(s, 2).tolist()[0] == "EF" and mid(s, 2, 3).tolist()[0] == "BCD"
    assert proper(pd.Series(["ACME pte ltd"])).tolist() == ["Acme Pte Ltd"]
    assert isnumber(pd.Series(["1,200", "x", "3.5", None])).tolist() == [True, False, True, False]


def test_date_formulas():
    d = pd.Series(pd.to_datetime(["2026-01-31", "2024-02-29"]))
    assert edate(d, 1).dt.strftime("%Y-%m-%d").tolist() == ["2026-02-28", "2024-03-29"]
    assert eomonth(d, 0).dt.day.tolist() == [31, 29] and eomonth(d, -1).dt.strftime("%m-%d").tolist() == ["12-31", "01-31"]
    s = pd.Series(pd.to_datetime(["2026-03-02", "2026-03-09", None]))
    e = pd.Series(pd.to_datetime(["2026-03-06", "2026-03-05", "2026-03-06"]))
    assert networkdays(s, e).tolist() == [5, -3, pd.NA]
    assert networkdays(s, e, holidays=["2026-03-04"]).tolist()[0] == 4
    out = workday(pd.Series(pd.to_datetime(["2026-03-07", "2026-03-09", None])), 1)
    assert out.dt.strftime("%Y-%m-%d").tolist()[:2] == ["2026-03-09", "2026-03-10"] and pd.isna(out.iloc[2])
    assert workday(pd.Series(pd.to_datetime(["2026-03-09"])), -1).iloc[0] == pd.Timestamp("2026-03-06")
    assert workday(pd.Series(pd.to_datetime(["2026-03-06"])), 1, holidays=["2026-03-09"]).iloc[0] == \
        pd.Timestamp("2026-03-10")
    assert datedif(pd.Timestamp("2025-03-15"), pd.Timestamp("2026-03-14"), "M") == 11
    assert datedif(pd.Timestamp("2025-03-15"), pd.Timestamp("2026-03-15"), "Y") == 1
    assert datedif(pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-06")) == 5
    assert excel_format_to_strftime("dd/mm/yyyy hh:mm") == "%d/%m/%Y %H:%M"
    assert excel_format_to_strftime("d-mmm-yy") == "%d-%b-%y" and excel_format_to_strftime("mm:ss") == "%M:%S"
    assert excel_text(pd.Series(pd.to_datetime(["2026-03-06"])), "dd mmm yyyy").tolist() == ["06 Mar 2026"]


# ---------- clean / transform / combine / quality ----------

def test_normalisers():
    assert normalise_bool(pd.Series(["Y", "No", "1.0", "maybe", None, True])).tolist() == \
        [True, False, True, pd.NA, pd.NA, True]
    assert normalise_currency(pd.Series(["US$", "us dollar", "S$", "€", "rmb", "jpy", None, "xyz"])).tolist() == \
        ["USD", "USD", "SGD", "EUR", "CNY", "JPY", pd.NA, "XYZ"]
    assert normalise_currency(pd.Series(["Greenback"]), extra={"greenback": "USD"}).tolist() == ["USD"]
    assert normalise_side(pd.Series(["b", "Sold", "2", "SS", "?"])).tolist() == ["BUY", "SELL", "SELL", "SELL_SHORT",
                                                                                 pd.NA]
    assert normalise_country(pd.Series(["Singapore", "U.K.", "usa", "SGP", "Narnia"])).tolist() == \
        ["SG", "GB", "US", "SG", "NARNIA"]
    assert strip_accents(pd.Series(["Café Société"])).tolist() == ["Cafe Societe"]
    assert standardise_dates(pd.Series(["06/03/2026", "2026-03-07", "8 Mar 2026", "junk"])).tolist() == \
        ["2026-03-06", "2026-03-07", "2026-03-08", pd.NA]
    assert minmax_scale(pd.Series([0, 5, 10])).tolist() == [0, 0.5, 1] and minmax_scale(pd.Series([2, 2])).tolist() == [0, 0]
    z = zscore(pd.Series([1.0, 2.0, 3.0]))
    assert z.round(6).tolist() == [-1.0, 0.0, 1.0]
    assert outliers_iqr(pd.Series([10, 11, 12, 11, 10, 500])).tolist() == [False] * 5 + [True]


def test_transform_extensions():
    df = pd.DataFrame({"code": ["A10", "A2", "B1", "A1"], "prio": ["LOW", "HIGH", "MEDIUM", "HIGH"],
                       "grp": ["x", "x", "y", "y"], "v": [4, 1, 3, 2]})
    assert sort_natural(df, "code")["code"].tolist() == ["A1", "A2", "A10", "B1"]
    assert sort_custom(df, "prio", ["HIGH", "MEDIUM", "LOW"], then=["v"])["code"].tolist() == ["A2", "A1", "B1", "A10"]
    assert top_n(df, 1, "v", by="grp")["code"].tolist() == ["A10", "B1"]
    s = df.sort_values(["grp", "v"])
    nv = next_value(s, "v", by="grp")
    assert nv.iloc[[0, 2]].tolist() == [4, 3] and nv.iloc[[1, 3]].isna().all()
    assert rank_within(df, "v", by="grp").tolist() == [1, 2, 1, 2]
    assert rolling_within(s, "v", 2, by="grp", func="sum").tolist() == [1, 5, 2, 5]
    assert pct_change_within(s, "v", by="grp").round(2).tolist()[1] == 3.0
    nested = nest(df, "grp", "code")
    assert nested.set_index("grp")["code_list"].to_dict() == {"x": ["A10", "A2"], "y": ["B1", "A1"]}
    first, last = first_last(df, "grp", "v")
    assert sorted(first["code"]) == ["A1", "A2"] and sorted(last["code"]) == ["A10", "B1"]
    yes, no = filter_rows(TRADES, ("desk", "==", "FX"), ("amount", ">", 1000))
    assert yes["id"].tolist() == ["T1", "T5"] and len(no) == 3
    any_, _ = filter_rows(TRADES, ("desk", "==", "EM"), ("ccy", "==", "EUR"), how="any")
    assert any_["id"].tolist() == ["T3", "T4"]
    assert filter_text(TRADES, "rat")["id"].tolist() == ["T2"]
    assert filter_text(TRADES, r"^T[12]$", cols=["id"], regex=True)["id"].tolist() == ["T1", "T2"]
    assert filter_between(TRADES, "trade_date", "2026-03-03", "2026-03-04")["id"].tolist() == ["T2", "T3"]
    assert filter_between(TRADES, "amount", low=100)["id"].tolist() == ["T1", "T2", "T5"]


def test_reconcile():
    left = pd.DataFrame({"ref": ["A", "B", "C", "D"], "amt": [100.0, 200.0, 300.0, 1.0], "ccy": ["USD", "usd ", "EUR", "X"],
                         "ok": [True, False, True, True]})
    right = pd.DataFrame({"ref": ["A", "B", "C", "E"], "amt": [100.004, 201.0, 300.0, 2.0], "ccy": ["USD", "USD", "GBP", "Y"],
                          "ok": [True, True, True, True]})
    r = reconcile(left, right, "ref", tolerance={"amt": 0.01})
    st = r.set_index("ref")["status"].to_dict()
    assert st == {"A": "MATCHED", "B": "BREAK", "C": "BREAK", "D": "LEFT_ONLY", "E": "RIGHT_ONLY"}
    bc = r.set_index("ref")["break_cols"].to_dict()
    assert bc["B"] == "amt,ok" and bc["C"] == "ccy" and r.set_index("ref").loc["B", "amt_diff"] == -1.0
    assert recon_summary(r).set_index("status")["count"].to_dict() == {"BREAK": 2, "MATCHED": 1, "LEFT_ONLY": 1,
                                                                       "RIGHT_ONLY": 1}
    with pytest.raises(ValueError):
        reconcile(pd.concat([left, left]), right, "ref")


def test_quality_extensions():
    df = pd.DataFrame({"id": [1, 2], "v": ["a", None]})
    assert_unique(df, "id")
    with pytest.raises(ValueError):
        assert_unique(pd.concat([df, df]), ["id"])
    h = row_hash(df)
    assert h.str.len().tolist() == [16, 16] and h.iloc[0] != h.iloc[1] and row_hash(df).equals(h)
    a = add_audit_columns(df, "run-1", source="in.xlsx")
    assert {"row_hash", "run_id", "processed_at", "source"} <= set(a.columns) and a["processed_at"].str.endswith("Z").all()
    new = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "changed", "c"]})
    assert changed_rows(df, new, "id")["id"].tolist() == [2, 3]


# ---------- readers / excel_write / dates ----------

def test_workbook_helpers(tmp_path):
    from openpyxl import Workbook
    from openpyxl.workbook.defined_name import DefinedName
    p = tmp_path / "legacy.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Trade", "Amount"])
    ws.append(["T1", 10])
    ws.append(["T2", 20])
    ws["D1"] = "=SUM(B2:B3)"
    hidden = wb.create_sheet("Lookups")
    hidden["A1"] = "secret map"
    hidden.sheet_state = "hidden"
    wb.defined_names["Rates"] = DefinedName("Rates", attr_text="Data!$B$2:$B$3")
    wb.save(p)
    info = workbook_info(p)
    sheets = info["sheets"].set_index("sheet")
    assert sheets.loc["Lookups", "state"] == "hidden" and "Rates" in info["names"] and info["has_macros"] is False
    hits = find_in_workbook(p, "t2")
    assert hits[["sheet", "cell"]].values.tolist() == [["Data", "A3"]]
    assert read_formulas(p, "Data").values.tolist() == [["D1", "=SUM(B2:B3)"]]
    assert read_cell(p, "Data", "B2") == 10 and read_cell(p, "Data", "D1", formulas=True) == "=SUM(B2:B3)"
    assert read_range(p, "Data", "A1:B3")["Amount"].tolist() == [10, 20]
    write_cell(p, "Data", "B2", 99)
    assert read_cell(p, "Data", "B2") == 99
    unhide_all_sheets(p)
    assert workbook_info(p)["sheets"].set_index("sheet").loc["Lookups", "state"] == "visible"
    autofit_columns(p)
    from openpyxl import load_workbook
    assert load_workbook(p)["Data"].column_dimensions["A"].width >= 5
    outs = sheets_to_csv(p, tmp_path / "csv")
    assert sorted(o.name for o in outs) == ["Data.csv", "Lookups.csv"]
    assert col_letter(28) == "AB" and col_index("AB") == 28 and col_index(col_letter(16384)) == 16384


def test_dates_extensions():
    hol = ["2026-04-03"]                                     # Good Friday, as text
    assert adjust_business_day(date(2026, 2, 28), convention="following") == date(2026, 3, 2)
    assert adjust_business_day(date(2026, 2, 28)) == date(2026, 2, 27)
    assert adjust_business_day(date(2026, 3, 1), convention="modified_preceding") == date(2026, 3, 2)
    assert adjust_business_day(date(2026, 4, 3), hol, "preceding") == date(2026, 4, 2)
    assert adjust_business_day("2026-03-07", convention="none") == date(2026, 3, 7)
    with pytest.raises(ValueError):
        adjust_business_day(date(2026, 3, 7), convention="nearest")
    assert add_business_days(date(2026, 4, 2), 1, hol) == date(2026, 4, 6)
    assert business_days_between("2026-04-02", "2026-04-07", hol) == 2
    assert is_business_day(pd.Timestamp("2026-04-03")) and not is_business_day("2026-04-03", hol)
    ny = pd.Series(pd.to_datetime(["2026-03-06 16:00", "2026-03-09 16:00"]))     # DST starts 8 Mar in NY
    sg = convert_tz(ny, "America/New_York", "Asia/Singapore")
    assert sg.dt.strftime("%Y-%m-%d %H:%M").tolist() == ["2026-03-07 05:00", "2026-03-10 04:00"]
    assert str(convert_tz(ny, "America/New_York", "UTC", naive_result=False).dt.tz) == "UTC"


def test_review_fixes_rules_and_dates(tmp_path):
    assert truthy(1.0) and not truthy(0.0) and truthy(np.int64(1)) and not truthy(pd.NA)
    mixed = pd.DataFrame({"id": [1, "A2", 3]})
    assert condition(mixed, "id", "eq", "1").tolist() == [True, False, False]
    with pytest.raises(ValueError):
        condition(mixed, "id", ">", 1)
    path = tmp_path / "r.xlsx"
    pd.DataFrame({"Code": ["BIG", "OFF", "ON"], "Column": ["amount", "desk", "desk"], "Op": ["gt", "eq", "eq"],
                  "Value": [1000000, "FX", "EM"], "Active": [1, 0, None]}).to_excel(path, index=False)
    r = rules_from_excel(path)
    assert r["code"].tolist() == ["BIG", "ON"] and r["value"].tolist()[0] == "1000000"
    for blank in (pd.NaT, None, np.nan, ""):
        with pytest.raises(BusinessError):
            add_business_days(blank, 1)
    hol = tmp_path / "hol.csv"
    hol.write_text("date\n2026/08/09\n09/08/2026\n2026-12-25\n")
    assert load_holidays(hol) == {date(2026, 8, 9), date(2026, 12, 25)}
    hol.write_text("date\n2026-12-25\nChristmas\n")
    with pytest.raises(ValueError):
        load_holidays(hol)
    amb = pd.Series(pd.to_datetime(["2026-11-01 01:30"]))           # happens twice in New York
    with pytest.raises(Exception):
        convert_tz(amb, "America/New_York", "UTC")
    assert convert_tz(amb, "America/New_York", "UTC", ambiguous="NaT").isna().all()


def test_blanks_never_match_negated_ops():
    df = pd.DataFrame({"ccy": ["USD", " ", "", None, "SGD"]})
    for op, v in [("not in", "USD"), ("!=", "USD"), ("not contains", "US")]:
        assert condition(df, "ccy", op, v).tolist() == [False, False, False, False, True], op
    assert condition(df, "ccy", "is null").tolist() == [False, True, True, True, False]
    assert condition(df, "ccy", "not null").tolist() == [True, False, False, False, True]
    assert condition(df, "ccy", "==", None).tolist() == [False, True, True, True, False]
    assert filter_rows(pd.DataFrame({"ccy": ["USD", "SGD"]}), "ccy", "==", "usd")[0]["ccy"].tolist() == ["USD"]
    assert summarize(pd.DataFrame({"g": ["a", "a"], "v": [1, 2]}), ["g"], n=["v", "count"])["n"].tolist() == [2]


def test_rule_values_fail_loud(tmp_path):
    empty = pd.DataFrame(columns=["code", "column", "op", "value", "active"])          # every rule switched off
    assert apply_rules(pd.DataFrame({"a": [1]}), empty)["flags_count"].tolist() == [0]
    assert validate_rules(empty, pd.DataFrame({"a": [1]})) == []
    dates = pd.DataFrame({"d": pd.to_datetime(["2026-03-01", None])})
    assert condition(dates, "d", "in", "2026-03-01").tolist() == [True, False]          # blank date never matches
    with pytest.raises(ValueError, match="not a date"):
        condition(dates, "d", "in", "2026-03-01, x")
    nums = pd.DataFrame({"a": [1500.0, 200.0]})
    with pytest.raises(ValueError, match="not a number"):
        condition(nums, "a", ">", "abc")
    with pytest.raises(ValueError, match="needs two values"):
        condition(nums, "a", "between", "5")
    with pytest.raises(ValueError, match="thousands commas"):
        condition(nums, "a", "between", "1,000, 2,000")
    assert condition(nums, "a", "between", "1,000; 2,000").tolist() == [True, False]
    assert condition(nums, "a", "in", "100, 200, 300").tolist() == [False, True]


def test_rule_values_second_review():
    nums = pd.DataFrame({"a": [1.5, 1500.0]})
    for v in ("1,5", "1.000,50"):
        with pytest.raises(ValueError, match="ambiguous"):
            condition(nums, "a", ">", v)
    assert condition(nums, "a", ">", "1,234.5").tolist() == [False, True]
    for op in ("gt", "in", "contains"):
        with pytest.raises(ValueError, match="needs a value"):
            condition(nums, "a", op, None)
    with pytest.raises(ValueError, match="above high"):
        condition(nums, "a", "between", "2000; 100")
    with pytest.raises(ValueError, match="type Y or N"):
        apply_rules(nums, pd.DataFrame([{"code": "BIG", "column": "a", "op": "gt", "value": "1000", "active": "Active"}]))
    assert apply_rules(nums, pd.DataFrame([{"code": "BIG", "column": "a", "op": "gt", "value": "1000",
                                            "active": 1.0}]))["flags"].tolist() == ["", "BIG"]
    probs = validate_rules(pd.DataFrame([{"code": "X", "column": "a", "op": "gt", "value": None},
                                         {"code": "Y", "column": "a", "op": "between", "value": "5"},
                                         {"code": "Z", "column": "d", "op": "in", "value": "2026-01-01, x"}]),
                           pd.DataFrame({"a": [1.0], "d": pd.to_datetime(["2026-01-01"])}))
    assert [p.split(":")[0] for p in probs] == ["X", "Y", "Z"]
