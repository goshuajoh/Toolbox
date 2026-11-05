import numpy as np
import pandas as pd
import pytest

from toolbank import *


def test_filter_formula_multirow():
    df = pd.DataFrame({"Ref": ["A1", "A2", "A3"], "Amt": [1.0, 2.0, 3.0]})
    yes, no = filter_split(df, df["Amt"] > 1)
    assert len(yes) == 2 and len(no) == 1
    assert case_when(df, [(df["Amt"] >= 3, "big"), (df["Amt"] >= 2, "mid")], "small").tolist() == ["small", "mid", "big"]
    assert case_when(df, [(pd.Series([True, pd.NA, False], dtype="boolean"), "x")], "-").tolist() == ["x", "-", "-"]
    assert case_when(df, [(df["Amt"] > 1, df["Ref"])], "").tolist() == ["", "A2", "A3"]
    assert multi_field(df, ["Amt"], lambda s: s * 2)["Amt"].tolist() == [2, 4, 6]
    g = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]})
    assert prev_value(g, "v", by="g").isna().tolist() == [True, False, True]
    assert running_total(g, "v", by="g").tolist() == [1, 3, 3]
    assert record_id(g, by="g")["record_id"].tolist() == [1, 2, 1]
    assert record_id(g)["record_id"].tolist() == [1, 2, 3]
    assert tile(pd.Series(range(8)), 4, labels=[1, 2, 3, 4]).tolist() == [1, 1, 2, 2, 3, 3, 4, 4]
    b = buckets(pd.Series([0, 2, 4, 7, 20]), [-np.inf, 0, 3, 5, 10, np.inf], ["cur", "1-3", "4-5", "6-10", ">10"])
    assert b.astype(str).tolist() == ["cur", "1-3", "4-5", "6-10", ">10"]
    ed = expand_dates(pd.DataFrame({"s": [pd.Timestamp("2026-03-06")], "e": [pd.Timestamp("2026-03-10")]}), "s", "e", "B")
    assert len(ed) == 3


def test_text_tools():
    tc = text_to_columns(pd.DataFrame({"x": ["a-b-c", "d-e"]}), "x", "-", names=["p", "q", "r"])
    assert tc["r"].isna().tolist() == [False, True]
    assert text_to_columns(pd.DataFrame({"x": ["a b"]}), "x", " ")["x_2"][0] == "b"
    assert text_to_rows(pd.DataFrame({"id": [1], "x": ["A; B;C"]}), "x", ";")["x"].tolist() == ["A", "B", "C"]
    rx = regex_extract(pd.DataFrame({"n": ["PAY USD 1,200.50 ref"]}), "n", r"(?P<ccy>[A-Z]{3})\s*(?P<amt>[\d,.]+)")
    assert rx["ccy"][0] == "USD" and rx["amt"][0] == "1,200.50"
    assert regex_flag(pd.Series(["Bank charges", None]), r"charge").tolist() == [True, False]


def test_unique_sample():
    dd = pd.DataFrame({"k": ["a", "a", "b"], "t": [1, 2, 3]})
    assert len(find_duplicates(dd, ["k"])) == 2
    u1, u2 = unique_split(dd, ["k"])
    assert len(u1) == 2 and len(u2) == 1
    assert keep_latest(dd, ["k"], "t").sort_values("k")["t"].tolist() == [2, 3]
    r = sample_rows(dd, 1, "random", by="k")
    assert len(r) == 2 and "k" in r.columns
    assert len(sample_rows(dd, 2, "last")) == 2


def test_join_lookup_sumifs():
    L = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]})
    R = pd.DataFrame({"id": [2, 3, 4], "v": ["x", "y", "z"]})
    lo, jn, ro = join3(L, R, "id")
    assert lo["id"].tolist() == [1] and jn["id"].tolist() == [2, 3] and ro["id"].tolist() == [4] and "v_r" in jn
    _, jn2, ro2 = join3(L.rename(columns={"id": "a"}), R.rename(columns={"id": "b"}), "a", "b")
    assert len(jn2) == 2 and ro2["b"].tolist() == [4]
    with pytest.raises(Exception):
        join3(L, pd.concat([R, R]), "id", validate="many_to_one")
    assert outer_join(L, R, "id")["_merge"].value_counts()["both"] == 2

    lk = lookup(pd.DataFrame({"ccy": ["USD ", "JPY"]}), pd.DataFrame({"ccy": ["USD", "EUR"], "rate": [1.35, 1.45]}),
                "ccy", "rate", default=0)
    assert lk["rate"].tolist() == [1.35, 0]
    assert lookup(pd.DataFrame({"acct": [123]}), pd.DataFrame({"acct": ["123"], "nm": ["X"]}), "acct", "nm")["nm"][0] == "X"
    with pytest.raises(ValueError, match="duplicate"):
        lookup(L, pd.DataFrame({"id": [1, 1], "z": [1, 2]}), "id", "z")

    sf = sumifs(pd.DataFrame({"c": ["a", "b", "z"]}), pd.DataFrame({"c": ["a", "a", "b"], "v": [1, 2, 3]}), "c", "v")
    assert sf["sum_v"].tolist() == [3, 3, 0]
    assert sumifs(pd.DataFrame({"c": ["a"]}), pd.DataFrame({"c": ["a", "a"], "v": [1, 2]}), "c", "v", "count")["count_v"][0] == 2
    g = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]})
    assert group_value(g, "g", "v").tolist() == [3, 3, 3]
    assert len(join_multiple([L, R.rename(columns={"v": "w"}), pd.DataFrame({"id": [1], "z": [9]})], on="id")) == 4
    un = union([L, R.assign(extra=1)], names=["L", "R"])
    assert len(un) == 6 and un["extra"].isna().sum() == 3 and set(un["source"]) == {"L", "R"}
    assert len(append_fields(L, pd.DataFrame({"d": [1, 2]}))) == 6


def test_fuzzy_and_diff():
    fz = fuzzy_match(["Acme Pte Ltd", "Globex Corporation", "Nothing"], ["ACME PTE. LTD.", "Globex Corp", "Initech"])
    assert fz["match"].tolist()[:2] == ["ACME PTE. LTD.", "Globex Corp"] and pd.isna(fz["match"][2])
    old = pd.DataFrame({"k": [1, 2, 3], "x": ["a", "b", None], "y": [1.0, 2.0, 3.0]})
    new = pd.DataFrame({"k": [2, 3, 4], "x": ["b", None, "d"], "y": [2.0, 3.5, 4.0]})
    ad, rm, ch = compare_frames(old, new, "k")
    assert ad["k"].tolist() == [4] and rm["k"].tolist() == [1] and ch[["k", "column"]].values.tolist() == [[3, "y"]]
    _, _, ch2 = compare_frames(old.astype({"x": "string"}), new.astype({"x": "string"}), "k")
    assert len(ch2) == 1


def test_match_with_tolerance():
    L = pd.DataFrame({"amt": [100.00, 50.0, None, 10.0], "d": pd.to_datetime(["2026-03-06"] * 4)})
    B = pd.DataFrame({"amt": [100.01, 50.0, 10.0, 7.0], "d": pd.to_datetime(["2026-03-07", "2026-03-06", "2026-03-20", "2026-03-06"])})
    m, ul, ub = match_with_tolerance(L, B, amt_l="amt", amt_r="amt", date_l="d", date_r="d")
    assert m["index"].tolist() == [0, 1] and m["diff_cents"].tolist() == [1, 0]
    assert ul["reason"].tolist() == ["missing amount/date", "no match in window"]
    assert len(ub) == 2


def test_reshape():
    g = pd.DataFrame({"g": ["a", "a", "b"], "v": [1, 2, 3]})
    assert summarize(g, ["g"], total=("v", "sum"), n=("v", "count"))["total"].tolist() == [3, 3]
    pv = pivot(pd.DataFrame({"d": ["FX", "FX", "R"], "c": ["USD", "SGD", "USD"], "v": [1, 2, 3]}), "d", "c", "v")
    assert pv.loc[pv["d"] == "Total", "Total"].iloc[0] == 6
    pv2 = pivot(pd.DataFrame({"d": ["FX"], "c": ["USD"], "v": [1], "w": [2]}), "d", "c", ["v", "w"], totals=False)
    assert "v | USD" in pv2.columns
    assert len(unpivot(pd.DataFrame({"id": [1], "Jan": [5], "Feb": [6]}), ["id"])) == 2
    st = add_subtotals(pd.DataFrame({"desk": ["FX", "FX", "R"], "ccy": ["U", "S", "U"], "v": [1, 2, 3]}), "desk", ["v"])
    assert st["desk"].tolist() == ["FX", "FX", "FX Subtotal", "R", "R Subtotal", "Grand Total"]
    assert st["v"].tolist() == [1, 2, 3, 3, 3, 6]
    assert share_of_total(g, "v", by="g").tolist() == [1 / 3, 2 / 3, 1.0]


def test_quality():
    df = pd.DataFrame({"Ref": ["A1", "A2", "A3"], "Amt": [1.0, None, 3.0]})
    pr = profile(df)
    assert pr.loc[pr.column == "Amt", "max"].iloc[0] == 3 and pr.loc[pr.column == "Amt", "nulls"].iloc[0] == 1
    assert check_schema(df, {"Amt": "number", "Ref": "text", "Zz": "any"}) == ["missing column Zz"]
    assert check_schema(df, {"Amt": "date"})[0].startswith("Amt should be date")
    v = validate(df, {"MISSING_AMT": lambda d: d["Amt"].isna(), "BIG": lambda d: d["Amt"] > 2}, "Ref")
    assert v.values.tolist() == [["A2", "MISSING_AMT"], ["A3", "BIG"]]
    ct = control_totals(df.fillna(0), "Amt", "Ref")
    assert ct == {"rows": 3, "sum": 4.0, "abs_sum": 4.0, "distinct_keys": 3}
    assert_totals_match(ct, dict(ct))
    with pytest.raises(ValueError):
        assert_totals_match(ct, {**ct, "sum": 5.0})


def test_lookup_case_and_spaces():
    L = pd.DataFrame({"ccy": ["usd ", "JPY", "Hong  Kong"]})
    R = pd.DataFrame({"ccy": ["USD", "EUR", "hong kong"], "rate": [1.35, 1.45, 0.17]})
    assert lookup(L, R, "ccy", "rate", default=0)["rate"].tolist() == [1.35, 0, 0.17]
    assert lookup(L, R, "ccy", "rate", default=0, case=True)["rate"].tolist() == [0, 0, 0]
    with pytest.raises(ValueError, match="duplicate"):
        lookup(L, pd.DataFrame({"ccy": ["USD", "usd"], "rate": [1, 2]}), "ccy", "rate")


def test_lookup_blank_keys_defaults_and_types():
    rates = pd.DataFrame({"ccy": ["USD", "EUR", None, "XYZ"], "rate": [1.35, 1.45, 999.0, np.nan]})
    trades = pd.DataFrame({"ccy": ["USD", None, "   ", "XYZ", "QQQ"]})
    out = lookup(trades, rates, "ccy", "rate", default=0)["rate"]
    assert out.tolist()[:3] == [1.35, 0.0, 0.0] and pd.isna(out.iloc[3]) and out.iloc[4] == 0   # blank in ref stays blank
    ids = pd.DataFrame({"acct": [1234567.0, np.nan]})
    assert lookup(ids, pd.DataFrame({"acct": ["1234567"], "nm": ["Alice"]}), "acct", "nm")["nm"].tolist()[0] == "Alice"
    T = pd.DataFrame({"d": pd.to_datetime(["2026-03-06 14:30", "2026-03-07"], format="mixed")})
    X = pd.DataFrame({"d": pd.to_datetime(["2026-03-07"]), "rate": [1.5]})
    assert lookup(T, X, "d", "rate")["rate"].tolist()[1] == 1.5
    assert lookup(pd.DataFrame({"k": ["Hong\u00a0Kong"]}), pd.DataFrame({"k": ["hong kong"], "v": [3]}), "k", "v")["v"].tolist() == [3]
    with pytest.raises(ValueError, match=r"\{'ACME LTD': \['Acme  Ltd', 'ACME LTD', 'acme ltd '\]\}"):
        lookup(pd.DataFrame({"k": ["a"]}), pd.DataFrame({"k": ["Acme  Ltd", "ACME LTD", "acme ltd "], "v": [1, 2, 3]}), "k", "v")
    assert lookup(pd.DataFrame({"k": [None]}), pd.DataFrame({"k": [None, None], "v": [1, 2]}), "k", "v")["v"].isna().all()
