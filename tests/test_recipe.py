import json
from pathlib import Path

import pandas as pd
import pytest

from toolbank import *

SPEC = """
name: fx breaks
inputs:
  rates: {fn: read_sheet, path: rates.xlsx}
steps:
  - {fn: read_sheet, path: trades.xlsx, required: [trade_date, Amount, ccy]}
  - {fn: cleanse, case: upper}
  - {fn: filter_rows, conditions: [[ccy, in, "USD,SGD"]], pick: 0, label: majors}
  - {fn: lookup, ref: $rates, key: ccy, cols: rate}
  - {fn: control_totals, amount_col: rate, as: totals}
  - {fn: safe_write_excel, path: out/breaks.xlsx, sheets: {Breaks: $df}, overwrite: true, as: report}
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({"Trade Date": ["06/03/2026", "07/03/2026", "08/03/2026"], "Amount": ["1,000", "2,000", "x"],
                  "ccy": ["usd", "SGD ", "EUR"]}).to_excel("trades.xlsx", index=False)
    pd.DataFrame({"ccy": ["USD", "SGD"], "rate": [1.35, 1.0]}).to_excel("rates.xlsx", index=False)
    (tmp_path / "r.yaml").write_text(SPEC)
    return tmp_path


def test_load_validate_run(files):
    r = load_recipe("r.yaml")
    assert r == load_recipe(SPEC) == load_recipe(r) == load_recipe(Path("r.yaml")) and validate_recipe(r) == []
    out, trace = run_recipe(r, log=lambda m: None)
    assert out["ccy"].tolist() == ["USD", "SGD"] and out["rate"].tolist() == [1.35, 1.0]
    assert trace["step"].tolist() == ["read_sheet", "cleanse", "majors", "lookup", "control_totals", "safe_write_excel"]
    assert trace["rows_out"].tolist() == [3, 3, 2, 2, 2, 2] and (trace["status"] == "OK").all()
    assert (files / "out" / "breaks.xlsx").exists()


def test_generated_code_runs(files):
    r = load_recipe("r.yaml")
    code = recipe_to_code(r, path="gen.py")
    assert code == recipe_to_code(r) == (files / "gen.py").read_text(encoding="utf-8")   # deterministic
    assert "def run(df=None):" in code and "rates = read_sheet(path='rates.xlsx')" in code
    assert "df = filter_rows(df, ['ccy', 'in', 'USD,SGD'])[0]  # majors" in code
    assert "df = lookup(df, ref=rates, key='ccy', cols='rate')" in code
    assert "report = safe_write_excel(path='out/breaks.xlsx', sheets={'Breaks': df}, overwrite=True)" in code
    ns = {}
    exec(compile(code, "gen.py", "exec"), ns)
    assert ns["run"]().shape == (2, 4)


def test_yaml_values_round_trip(files):
    # dates, zero-padded ids, NO / on, $$ literals and nan agree between run_recipe and the generated script
    r = load_recipe("steps:\n  - {fn: read_sheet, path: trades.xlsx}\n  - {fn: to_date_col, as: x}\n"
                    .replace("  - {fn: to_date_col, as: x}\n", "")
                    + "  - {fn: filter_rows, conditions: [[Amount, '==', '$$1,000'], [ccy, '!=', NO]], how: any, pick: 0}\n"
                    + "  - {fn: lookup, ref: $rates, key: ccy, cols: rate, default: 00123}\n"
                    + "inputs:\n  rates: {fn: read_sheet, path: rates.xlsx}\n")
    out, _ = run_recipe(r, log=lambda m: None)
    assert out["Amount"].tolist() == ["1,000", "2,000", "x"] and out["rate"].tolist() == [1.35, 1.0, "00123"]
    code = recipe_to_code(r)
    assert "'$1,000'" in code and "'NO'" in code and "default='00123'" in code
    ns = {}
    exec(compile(code, "gen.py", "exec"), ns)
    assert ns["run"]()["rate"].tolist() == out["rate"].tolist()
    d = {"steps": [{"fn": "read_sheet", "path": "trades.xlsx"},
                   {"fn": "filter_between", "col": "Amount", "low": __import__("datetime").date(2026, 3, 1)}]}
    code = recipe_to_code(d)
    assert "low=pd.Timestamp('2026-03-01')" in code and compile(code, "g", "exec")
    assert "float('nan')" in recipe_to_code({"steps": [{"fn": "read_sheet", "path": "x", "header_row": float("nan")}]})


def test_validation_messages():
    bad = {"steps": [{"fn": "read_shet", "path": "x"}, {"fn": "lookup", "refkey": 1, "ref": "$rates"},
                     {"fn": "cleanse", "df": 1, "pick": True, "as": "class"}, {"fn": "filter_rows", "conditions": "ccy == x"},
                     "oops", {"fn": "safe_write_excel", "path": "o.xlsx", "sheets": {}, "overwrtie": True, "as": "cleanse"},
                     {"fn": "cleanse", True: "x", "label": 3}, {"fn": "reconcile", "right": "$df", "on": "k"}]}
    probs = validate_recipe(bad)
    assert probs == [
        "steps[0] (read_shet): unknown function 'read_shet'; did you mean ['read_sheet', 'read_all_sheets', 'read_since']?",
        "steps[1] (lookup): unknown parameter 'refkey'; did you mean 'ref_key'?",
        "steps[1] (lookup): parameter 'key' is required", "steps[1] (lookup): parameter 'cols' is required",
        "steps[1] (lookup): $rates is not an input or an earlier 'as:' name (known: ['df'])",
        "steps[2] (cleanse): 'df' is passed automatically (the current frame); do not set it",
        "steps[2] (cleanse): pick must be a whole number",
        "steps[2] (cleanse): 'as' must be a plain variable name, not 'class'",
        "steps[3] (filter_rows): 'conditions' must be a list with one entry per argument "
        "(e.g. [[ccy, in, 'USD,SGD']]), not str",
        "steps[4]: needs fn: <toolbank function>",
        "steps[5] (safe_write_excel): unknown parameter 'overwrtie'; did you mean 'overwrite'?",
        "steps[5] (safe_write_excel): 'cleanse' is the name of a toolbank function; choose another name",
        "steps[6] (cleanse): parameter key True is not a plain name (a YAML 1.1 loader turns on / yes / no into "
        "booleans: read the file with load_recipe, or quote the key: 'on': ccy)",
        "steps[6] (cleanse): label must be text",
        "steps[7] (reconcile): parameter 'left' is required (the current frame is $df)"]
    assert validate_recipe({"steps": [{"fn": "cleanse"}]}) == [
        "steps[0] (cleanse): takes a frame as its first argument but no step produced one yet; give run_recipe a df "
        "or start with a reader"]
    assert validate_recipe({"steps": [{"fn": "cleanse"}]}, with_input=True) == []
    assert validate_recipe("nope") == ["recipe must be a mapping with 'steps'"]
    assert validate_recipe({"steps": []}) == ["steps must be a non-empty list"]
    assert validate_recipe({"inputs": {"df": {"fn": "read_sheet", "path": "x"}}, "steps": [{"fn": "read_sheet", "path": "y"}]}) \
        == ["inputs.df: 'df' is reserved ('df' is the current frame)"]
    assert validate_recipe({"inputs": {"clean": {"fn": "cleanse", "case": "upper", "as": "z"}},
                            "steps": [{"fn": "read_sheet", "path": "y"}]}) == [
        "inputs.clean: parameter 'df' is required (an input gets no frame: give it as df: $other_input)",
        "inputs.clean: 'as' and 'label' are for steps, not inputs"]
    assert validate_recipe({"steps": [{"fn": "read_sheet", "path": "x", "as": "raw"}, {"fn": "cleanse"}]}) == [
        "steps[0] (read_sheet): 'as' before any step produced a frame; load it under inputs: instead",
        "steps[1] (cleanse): takes a frame as its first argument but no step produced one yet; give run_recipe a df "
        "or start with a reader"]
    assert validate_recipe({"steps": [{"fn": "read_sheet", "path": "x"}, {"fn": "cleanse", "as": ["a"]}]}) == [
        "steps[1] (cleanse): 'as' must be a plain variable name, not ['a']"]
    with pytest.raises(ValueError, match=r"bad recipe: steps\[0\]"):
        load_recipe({"steps": [{"fn": "nope"}]})


def test_file_handling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="recipe file not found: missing.yaml"):
        load_recipe("missing.yaml")
    with pytest.raises(FileNotFoundError):
        load_recipe(Path("missing.yaml"))
    with pytest.raises(ValueError, match="plain text"):
        load_recipe("just words")
    (tmp_path / "r.json").write_text(json.dumps({"steps": [{"fn": "read_sheet", "path": "x"}]}, indent="\t"))
    assert load_recipe("r.json")["steps"][0]["fn"] == "read_sheet"
    (tmp_path / "ids.yaml").write_text("steps:\n  - {fn: read_sheet, path: x, sheet: 01, required: [00123]}\n")
    assert load_recipe("ids.yaml")["steps"][0]["sheet"] == "01"
    assert load_recipe("steps:\n  - {fn: read_sheet, path: x, on: 1}\n")["steps"][0]["on"] == 1   # key stays text


def test_runtime_errors_and_pick(files):
    pd.DataFrame({"g": ["a", "a"], "v": [1, 2]}).to_excel("g.xlsx", index=False)
    with pytest.raises(RobotError, match="returned dict, not a DataFrame") as e:
        run_recipe({"steps": [{"fn": "read_sheet", "path": "g.xlsx"}, {"fn": "control_totals", "amount_col": "v"}]},
                   log=lambda m: None)
    assert e.value.code == "RECIPE"
    with pytest.raises(RobotError, match="pick given but read_sheet returned DataFrame"):
        run_recipe({"steps": [{"fn": "read_sheet", "path": "g.xlsx", "pick": 0}]}, log=lambda m: None)
    with pytest.raises(RobotError, match="out of range"):
        run_recipe({"steps": [{"fn": "read_sheet", "path": "g.xlsx"}, {"fn": "filter_rows", "conditions": [["v", ">", 1]],
                                                                        "pick": 5}]}, log=lambda m: None)
    out, _ = run_recipe({"steps": [{"fn": "read_sheet", "path": "g.xlsx"}, {"fn": "summarize", "by": "g", "n": ["v", "count"]}]},
                        log=lambda m: None)
    assert out.to_dict("records") == [{"g": "a", "n": 2}]
    with_input = {"inputs": {"both": {"fn": "filter_rows", "df": "$raw", "conditions": [["v", ">", 1]]},
                             "raw": {"fn": "read_sheet", "path": "g.xlsx"}}, "steps": [{"fn": "read_sheet", "path": "g.xlsx"}]}
    assert validate_recipe(with_input)[0].startswith("inputs.both: $raw is not an input")
    r = {"inputs": {"raw": {"fn": "read_sheet", "path": "g.xlsx"}, "big": {"fn": "filter_rows", "df": "$raw",
                    "conditions": [["v", ">", 1]], "pick": 0}},
         "steps": [{"fn": "read_sheet", "path": "g.xlsx"}, {"fn": "union", "frames": ["$df", "$big"]}]}
    assert validate_recipe(r) == []
    out, _ = run_recipe(r, log=lambda m: None)
    code = recipe_to_code(r)
    assert len(out) == 3 and "big = filter_rows(raw, ['v', '>', 1])[0]" in code
    ns = {}
    exec(compile(code, "gen.py", "exec"), ns)
    assert len(ns["run"]()) == 3
    assert "excel_run_macro('b.xlsm', 'Refresh', 1, 2, save=False)" in recipe_to_code(
        {"steps": [{"fn": "read_sheet", "path": "g.xlsx"},
                   {"fn": "excel_run_macro", "path": "b.xlsm", "macro": "Refresh", "args": [1, 2], "save": False, "as": "rc"}]})


def test_recipe_with_input_frame():
    r = {"steps": [{"fn": "cleanse", "case": "upper"}, {"fn": "filter_rows", "conditions": [["a", "==", "X"]], "pick": 0}]}
    out, trace = run_recipe(r, df=pd.DataFrame({"a": [" x ", "y"]}), log=lambda m: None)
    assert out["a"].tolist() == ["X"] and trace["rows_in"].tolist() == [2, 2]
    code = recipe_to_code(r)
    assert "def run(df):" in code and "out = run(read_any(sys.argv[1]))" in code
    compile(code, "gen.py", "exec")
    with pytest.raises(ValueError, match="start with a reader"):
        run_recipe(r)
    flow = recipe_to_flow({"steps": [{"fn": "cleanse", "label": "cleanse_2"}, {"fn": "cleanse"}, {"fn": "cleanse"}]},
                          log=lambda m: None, keep=True)
    flow.run(pd.DataFrame({"a": ["A"]}))
    assert flow.trace["step"].tolist() == ["cleanse_2", "cleanse", "cleanse_3"] and len(flow.snapshots) == 3
    assert "  # load the file" in recipe_to_code({"steps": [{"fn": "read_sheet", "path": "x", "label": "load\nthe   file"}]})
