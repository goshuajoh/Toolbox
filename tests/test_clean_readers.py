import pandas as pd
import pytest
from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.table import Table

from toolbank import *


def test_to_number_and_dates():
    assert to_number(pd.Series(["1,234.50", "(10.00)", "S$ 12", "x"])).tolist()[:3] == [1234.5, -10.0, 12.0]
    d = to_date(pd.Series(["2026-03-06", "06/03/2026", "9 Mar 2026", None, ""]))
    assert [x.strftime("%Y-%m-%d") for x in d[:3]] == ["2026-03-06", "2026-03-06", "2026-03-09"]
    assert d[3:].isna().all()
    assert excel_serial_to_date(pd.Series([46087]))[0] == pd.Timestamp("2026-03-06")
    assert yyyymmdd_to_date(pd.Series([20260306, "20260307"])).dt.day.tolist() == [6, 7]
    assert cents(1.10 + 2.20) == cents(3.30)


def test_select_rename_autotypes():
    df = pd.DataFrame({"Ref": ["A"], "Amt (SGD)": [1]})
    assert list(select(df, rename={"Ref": "ref"}, order=["Amt (SGD)"]).columns) == ["Amt (SGD)", "ref"]
    assert list(dynamic_rename(df, r"\s*\(.*\)").columns) == ["Ref", "Amt"]
    assert list(snake_columns(df).columns) == ["ref", "amt_sgd"]
    a = auto_types(pd.DataFrame({"n": ["1,000", "2"], "id": ["0012", "0034"], "d": ["06/03/2026", "2026-03-07"]}))
    assert a["n"].tolist() == [1000.0, 2.0] and a["id"].tolist() == ["0012", "0034"] and a["d"].dt.day.tolist() == [6, 7]


def test_cleanse_family():
    c = cleanse(pd.DataFrame({"x": ["  a  b ", "", None, "c\t"], "n": [1, 2, 3, 4]}), case="upper")
    assert c["x"][0] == "A B" and pd.isna(c["x"][1]) and pd.isna(c["x"][2]) and c["x"][3] == "C"
    assert c["n"].tolist() == [1, 2, 3, 4]
    assert drop_empty(pd.DataFrame({"a": [1, None], "b": [None, None]})).shape == (1, 1)
    assert fill_down(pd.DataFrame({"g": ["X", None, "Y", None]}), "g")["g"].tolist() == ["X", "X", "Y", "Y"]
    assert impute(pd.DataFrame({"v": [1, None, 3]}), "v")["v"].tolist() == [1, 2, 3]
    assert impute(pd.DataFrame({"v": [1, None]}), "v", "value", 0)["v"].tolist() == [1, 0]
    assert map_values(pd.Series(["usa ", "U.K.", "SG"]), {"USA": "US", "U.K.": "GB"}).tolist() == ["US", "GB", "SG"]
    t = pd.DataFrame({"f": ["SGP"], "r": ["SG"]})
    assert find_replace(pd.Series(["SGP", "XX"]), t, "f", "r").tolist() == ["SG", "XX"]
    t2 = pd.DataFrame({"f": ["Pte Ltd", "Pte"], "r": ["PL", "P"]})
    assert find_replace(pd.Series(["Pte Ltd Acme"]), t2, "f", "r", "contains")[0] == "PL Acme"
    assert normalise_key(pd.Series([" 12-3 ", "0123"]), width=6).tolist() == ["000123", "000123"]
    assert normalise_name(pd.Series(["Acme Pte. Ltd.", "ACME PTE LTD", "Tan & Sons Co"])).tolist() == ["ACME", "ACME", "TAN AND SONS"]
    assert composite_key(pd.DataFrame({"a": ["x", None], "b": [1, 2]}), ["a", "b"]).tolist() == ["x|1", "|2"]


def test_readers(tmp_path):
    df = pd.DataFrame({"Ref": ["A1", "A2", "A3"], "Amt": [1.0, 2.0, 3.0]})
    with pd.ExcelWriter(tmp_path / "two.xlsx") as w:
        pd.DataFrame([["Title"], [None]]).to_excel(w, sheet_name="S1", index=False, header=False)
        df.to_excel(w, sheet_name="S1", index=False, startrow=2)
        df.to_excel(w, sheet_name="S2", index=False)
    assert find_header_row(tmp_path / "two.xlsx", "S1", ["Ref", "Amt"]) == 3
    assert read_sheet(tmp_path / "two.xlsx", "S1", 3, required=["Ref"])["Amt"].sum() == 6
    with pytest.raises(ValueError, match="missing columns"):
        read_sheet(tmp_path / "two.xlsx", "S2", required=["Nope"])
    assert list_sheets(tmp_path / "two.xlsx") == ["S1", "S2"]
    assert "sheet" in read_all_sheets(tmp_path / "two.xlsx").columns

    df.to_csv(tmp_path / "a.csv", index=False)
    (tmp_path / "b.csv").write_bytes(("﻿Ref;Amt\nB1;10,5\n").encode("utf-8"))
    assert read_csv_smart(tmp_path / "b.csv").columns.tolist() == ["Ref", "Amt"]
    (tmp_path / "c.csv").write_bytes("Ref,Name\nC1,Caf\xe9\n".encode("cp1252"))
    assert read_csv_smart(tmp_path / "c.csv")["Name"][0] == "Café"
    f = read_folder(tmp_path / "*.csv")
    assert set(f["source_file"]) == {"a.csv", "b.csv", "c.csv"}
    assert list(read_any(tmp_path / "b.csv").columns) == ["Ref", "Amt"]


def test_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    df = pd.DataFrame({"Ref": ["A1"], "Amt": [1.0]})
    write_parquet(df, tmp_path / "d.parquet")
    assert read_parquet(tmp_path / "d.parquet").equals(df)


def test_excel_structures(tmp_path):
    wb = Workbook(); ws = wb.active; ws.title = "M"
    ws.append(["Desk", "Q1", None, "Q2", None]); ws.append([None, "Sales", "Cost", "Sales", "Cost"])
    ws.append(["FX", 1, 2, 3, 4]); ws.merge_cells("B1:C1"); ws.merge_cells("D1:E1")
    wb.save(tmp_path / "mh.xlsx")
    assert {"Q1 | Sales", "Q2 | Cost"} <= set(read_multirow_header(tmp_path / "mh.xlsx", "M").columns)

    wb = Workbook(); ws = wb.active; ws.title = "U"
    ws.append(["Desk", "Ccy", "Amt"]); ws.append(["FX", "USD", 1]); ws.append([None, "SGD", 2]); ws.merge_cells("A2:A3")
    wb.save(tmp_path / "um.xlsx")
    assert unmerge_fill(tmp_path / "um.xlsx", "U")["Desk"].tolist() == ["FX", "FX"]

    wb = Workbook(); ws = wb.active; ws.title = "Rates"
    for r in [["Ccy", "Rate"], ["USD", 1.35], ["EUR", 1.45]]:
        ws.append(r)
    ws.add_table(Table(displayName="FX", ref="A1:B3"))
    wb.defined_names["FXR"] = DefinedName("FXR", attr_text="Rates!$A$1:$B$3")
    wb.save(tmp_path / "tbl.xlsx")
    assert read_excel_table(tmp_path / "tbl.xlsx", "FX")["Rate"].tolist() == [1.35, 1.45]
    assert read_named_range(tmp_path / "tbl.xlsx", "FXR")["Ccy"].tolist() == ["USD", "EUR"]


def test_fixed_width_and_chunks(tmp_path):
    (tmp_path / "fw.txt").write_text("0001234567  1500.00\n0009999999   -20.50\n", encoding="cp1252")
    fw = read_fixed_width(tmp_path / "fw.txt", [("acct", 0, 10), ("amt", 10, 20)])
    assert fw["acct"].tolist() == ["0001234567", "0009999999"] and to_number(fw["amt"]).tolist() == [1500.0, -20.5]
    pd.DataFrame({"k": ["a", "b"] * 500, "v": range(1000)}).to_csv(tmp_path / "big.csv", index=False)
    cc = csv_in_chunks(tmp_path / "big.csv", lambda ch: ch.groupby("k", as_index=False)["v"].sum(), chunksize=300)
    assert cc["v"].sum() == sum(range(1000))


def test_to_number_edge_cases():
    import numpy as np
    s = pd.Series(["1,234.50", "(10.00)", "S$ 12", "x", "100.00-", "1,000 DR", "USD 1,200", "1.234,56", "10,5",
                   "− 5", "", None, "500 CR", "+7", "1.234.567", "€ 1 000,25", "3.5%"])
    out = to_number(s)
    assert out.dtype == "float64"
    assert out.tolist()[:3] == [1234.5, -10.0, 12.0] and np.isnan(out[3])
    assert out.tolist()[4:10] == [-100.0, -1000.0, 1200.0, 1234.56, 10.5, -5.0]
    assert out[10:12].isna().all() and out.tolist()[12:16] == [500.0, 7.0, 1234567.0, 1000.25] and np.isnan(out[16])
    assert to_number(pd.Series([1, 2])).tolist() == [1.0, 2.0] and to_number(pd.Series([True])).isna().all()


def test_to_date_serials_and_yyyymmdd():
    import numpy as np
    d = to_date(pd.Series(["20260306", "46087", "46087.0", "2026/08/09", "06/03/2026 14:30", "junk"]))
    assert d.dt.strftime("%Y-%m-%d %H:%M").tolist()[:5] == ["2026-03-06 00:00"] * 3 + ["2026-08-09 00:00",
                                                                                      "2026-03-06 14:30"]
    assert pd.isna(d.iloc[5])
    n = to_date(pd.Series([46087, 20260306, 5, np.nan]))
    assert n.dt.day.tolist()[:2] == [6, 6] and n.iloc[2:].isna().all()
    assert to_date(pd.Series([46087.0])).iloc[0] == pd.Timestamp("2026-03-06")


def test_headers_and_column_matching():
    df = pd.DataFrame([[1, 2, 3, 4, 5]], columns=["Trade Date ", "Amount", "Amount", None, "Unnamed: 4"])
    assert clean_headers(df).columns.tolist() == ["Trade Date", "Amount", "Amount_2", "col_4", "col_5"]
    assert norm_label(" Trade  Date: ") == norm_label("TRADE_DATE") == "trade date" and norm_label(None) == ""
    df = pd.DataFrame({"Trade Date": [1], "AMOUNT (SGD)": [2], "ccy": [3]})
    assert find_column(df, "trade_date") == "Trade Date" and find_column(df, "amount sgd") == "AMOUNT (SGD)"
    assert find_column(df, "zzz", required=False) is None
    with pytest.raises(MissingColumns, match=r"did you mean \['Trade Date'\]") as e:
        find_column(df, "Trade Dt")
    assert isinstance(e.value, KeyError) and isinstance(e.value, ValueError) and str(e.value).startswith("missing")
    with pytest.raises(MissingColumns, match=r"missing columns \['trade dte', 'amt'\]"):
        resolve_columns(df, ["trade dte", "amt", "ccy"])
    out = require_columns(df, ["trade_date", "Ccy"], source="in.xlsx")
    assert out.columns.tolist() == ["trade_date", "AMOUNT (SGD)", "Ccy"] and require_columns(df, []).equals(df)
    with pytest.raises(MissingColumns, match="^in.xlsx: missing"):
        require_columns(df, "nope", source="in.xlsx")
    std = standard_columns(df, {"trade_date": ["Trade Dt", "Trade Date"], "amount": "Amount (SGD)", "x": ["nope"]})
    assert std.columns.tolist() == ["trade_date", "amount", "ccy"]
    assert standard_columns(df, {"amount": "Amount (SGD)"}, keep_others=False).columns.tolist() == ["amount"]
    with pytest.raises(MissingColumns):
        standard_columns(df, {"x": ["nope"]}, strict=True)
    with pytest.raises(MissingColumns, match="several columns"):
        standard_columns(pd.DataFrame({"Amt": [1], "Amount": [2]}), {"amount": ["Amt"]})
    with pytest.raises(MissingColumns, match="ambiguous"):
        find_column(pd.DataFrame({"Amt": [1], "AMT": [2]}), "amt")
    with pytest.raises(ValueError, match="case must be"):
        cleanse(df, case="UPPER")
    assert cleanse(pd.DataFrame({"a": [" x "], "b": [" y "]}), cols="a")["b"].tolist() == [" y "]


def test_readers_tolerant_required(tmp_path):
    pd.DataFrame({"Trade Date ": ["2026-03-06"], "AMOUNT (SGD)": [1], "x": [None]}).to_excel(tmp_path / "a.xlsx",
                                                                                                index=False)
    df = read_sheet(tmp_path / "a.xlsx", required=["trade_date", "Amount (SGD)"])
    assert df.columns.tolist() == ["trade_date", "Amount (SGD)", "x"]
    with pytest.raises(ValueError, match=r"a.xlsx: missing columns \['Trade Dt'\]; did you mean") as e:
        read_sheet(tmp_path / "a.xlsx", required=["Trade Dt"])
    assert isinstance(classify(e.value), BusinessError)
    (tmp_path / "e.csv").write_text("")
    with pytest.raises(ValueError, match="is empty"):
        read_csv_smart(tmp_path / "e.csv")
    (tmp_path / "h.csv").write_text("Ref;Amt\nA;1\n")
    assert read_any(tmp_path / "h.csv", required=["ref"]).columns.tolist() == ["ref", "Amt"]
    assert read_any(tmp_path / "a.xlsx", required=["amount sgd"]).columns.tolist()[1] == "amount sgd"
